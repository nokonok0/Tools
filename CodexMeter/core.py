"""Read-only Codex log monitor. Persist numeric observations, never conversation text."""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DAY = 86400
MAX_READ = 2 * 1024 * 1024


def timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def default_home():
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


def data_directory():
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local"))) / "CodexMeter"


def normalize_window(raw):
    if not isinstance(raw, dict):
        return None
    used = raw.get("used_percent", raw.get("usedPercent"))
    minutes = raw.get("window_minutes", raw.get("windowDurationMins"))
    reset = raw.get("resets_at", raw.get("resetsAt"))
    try:
        used, minutes, reset = float(used), int(minutes), int(reset)
        if not 0 <= used <= 100 or minutes <= 0 or reset <= 0:
            return None
        return {"used": used, "minutes": minutes, "reset": reset}
    except (ValueError, TypeError, OverflowError):
        return None


def normalize(line, session, state):
    # Filter before parsing: tool output, images and user messages can be large.
    if not any(marker in line for marker in (b'"turn_context"', b'"event_msg"', b'"token_usage_record"')):
        return []
    try:
        obj = json.loads(line)
    except (ValueError, UnicodeError):
        return []
    ts = timestamp(obj.get("timestamp"))
    payload = obj.get("payload")
    if ts is None or not isinstance(payload, dict):
        return []
    base = {"ts": ts, "session": session}
    if obj.get("type") == "token_usage_record":
        raw = payload.get("usage")
        if not isinstance(raw, dict) or not state.get("model"):
            return []
        usage = {key: value for key, value in raw.items()
                 if key in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                            "output_tokens", "reasoning_output_tokens", "total_tokens")
                 and type(value) is int and value >= 0}
        if not usage:
            return []
        return [{**base, "kind": "usage", "turn": str(payload.get("turn_id") or state.get("turn", "")),
                 "model": state["model"], "effort": state.get("effort", "default"),
                 "record": hashlib.sha256(str(payload.get("response_id") or obj.get("ordinal") or ts).encode()).hexdigest()[:24],
                 "usage": usage}]
    if obj.get("type") == "turn_context":
        model = payload.get("model")
        if not isinstance(model, str) or not model:
            return []
        state.update(model=model[:120], effort=str(payload.get("effort") or "default")[:30],
                     turn=str(payload.get("turn_id") or ""))
        return [{**base, "kind": "context", **state}]
    if obj.get("type") != "event_msg":
        return []
    kind = payload.get("type")
    if kind in ("task_started", "task_complete", "turn_aborted"):
        return [{**base, "kind": "start" if kind == "task_started" else "end",
                 "turn": str(payload.get("turn_id") or state.get("turn") or ""),
                 "start": timestamp(payload.get("started_at"))}]
    if kind != "token_count":
        return []
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict):
        return []
    primary = normalize_window(limits.get("primary"))
    secondary = normalize_window(limits.get("secondary"))
    if not primary and not secondary:
        return []
    return [{**base, "kind": "quota", "turn": state.get("turn", ""),
             "model": state.get("model"), "effort": state.get("effort", "default"),
             "bucket": str(limits.get("limit_id") or "codex")[:120],
             "primary": primary, "secondary": secondary}]


@dataclass
class Cursor:
    offset: int
    state: dict
    partial: bytes = b""
    skip_line: bool = False


class LogReader:
    def __init__(self, home=None):
        self.home = Path(home or default_home())
        self.cursors = {}
        self.files = []
        self.discovered_at = 0
        self.truncated = False
        self.total_bytes = 0

    def discover(self, now):
        root = self.home / "sessions"
        if not root.is_dir():
            raise FileNotFoundError("Codex sessions folder unavailable")
        candidates = []
        for p in root.rglob("*.jsonl"):
            try:
                modified = p.stat().st_mtime
                if modified >= now - 7 * DAY:
                    candidates.append((modified, p))
            except OSError:
                continue
        candidates.sort(reverse=True)
        self.truncated = len(candidates) > 64
        self.files = [p for _, p in candidates[:64]]
        self.cursors = {p: c for p, c in self.cursors.items() if p in self.files}
        self.discovered_at = now

    def poll(self, now=None, force=False):
        now = time.time() if now is None else now
        if force or now - self.discovered_at >= 60:
            self.discover(now)
        events = []
        for p in self.files:
            try:
                size = p.stat().st_size
                cursor = self.cursors.get(p)
                if cursor is None or size < cursor.offset:
                    offset = max(0, size - 1024 * 1024)
                    cursor = Cursor(offset, {}, skip_line=offset > 0)
                    self.cursors[p] = cursor
                if size == cursor.offset:
                    continue
                with p.open("rb") as stream:
                    stream.seek(cursor.offset)
                    chunk = stream.read(MAX_READ)
                cursor.offset += len(chunk)
                self.total_bytes += len(chunk)
                data = cursor.partial + chunk
                cursor.partial = b""
                if cursor.skip_line:
                    newline = data.find(b"\n")
                    if newline < 0:
                        continue
                    data = data[newline + 1:]
                    cursor.skip_line = False
                lines = data.split(b"\n")
                cursor.partial = lines.pop()
                if len(cursor.partial) > MAX_READ:
                    cursor.partial = b""
                    cursor.skip_line = True
                # The path is hashed; neither paths nor conversation text are stored.
                session = hashlib.sha256(str(p).encode()).hexdigest()[:20]
                for line in lines:
                    events.extend(normalize(line, session, cursor.state))
            except OSError:
                continue
        return events


class Observations:
    def __init__(self, path=None):
        self.path = Path(path or data_directory() / "observations.json")
        self.events = []
        self.seen = set()
        self.dirty = False
        self.saved_at = 0
        self.history = {}
        self.archived_through = float('-inf')
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("version") in (1, 2) and isinstance(raw.get("events"), list):
                self.history = raw.get("history", {})
                self.archived_through = raw.get("archived_through") or float('-inf')
                self.add(raw["events"])
        except (OSError, ValueError, AttributeError):
            pass
        self.dirty = False

    def add(self, events, now=None):
        now = time.time() if now is None else now
        changed = False
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("ts"), (float, int)):
                continue
            if not self.archived_through < event["ts"] <= now + 60:
                continue
            key = self.event_key(event)
            if key not in self.seen:
                self.seen.add(key)
                self.events.append(event)
                changed = True
        if changed:
            self.events.sort(key=lambda e: e["ts"])
            self.seen = {self.event_key(e) for e in self.events}
            self.dirty = True
        return changed

    @staticmethod
    def event_key(event):
        if event.get("kind") == "usage":
            return (event.get("session"), "usage", event.get("record", event["ts"]))
        return (event.get("session"), event["ts"], event.get("kind"), event.get("bucket"))

    def save(self, force=False):
        now = time.time()
        if not self.dirty or (not force and now - self.saved_at < 120):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 2, "events": self.events,
                                        "history": self.history,
                                        "archived_through": self.archived_through if self.history else None},
                                        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.path)
        self.saved_at = now
        self.dirty = False


def build_view(events, now=None, incomplete=False, history=None):
    now = time.time() if now is None else now
    events = sorted(list((history or {}).get("contexts", [])) + list(events), key=lambda e: e["ts"])
    runs = {}
    latest = {}
    models = {}
    changes = []
    usage = defaultdict(lambda: defaultdict(int))
    for event in events:
        identity = (event.get("session"), event.get("turn"))
        kind = event.get("kind")
        if event.get("turn"):
            runs.setdefault(identity, {})["last_activity"] = event["ts"]
        if kind in ("context", "quota", "usage") and event.get("model"):
            key = (event["model"], event.get("effort", "default"))
            models[key] = max(models.get(key, 0), event["ts"])
        if kind == "context":
            changes.append(event)
            run = runs.setdefault(identity, {})
            run["key"] = (event["model"], event.get("effort", "default"))
        elif kind == "start":
            run = runs.setdefault(identity, {})
            run["start"] = event.get("start") or event["ts"]
        elif kind == "end":
            run = runs.setdefault(identity, {})
            run["end"] = event["ts"]
            if event.get("start"):
                run["start"] = event["start"]
        elif kind == "quota":
            latest[event["bucket"]] = event
        elif kind == "usage":
            key = (event["model"], event.get("effort", "default"))
            for name, value in event["usage"].items():
                usage[key][name] += value
    intervals = []
    for identity, run in runs.items():
        if "start" in run:
            # Crashed/abandoned turns may have no completion record. They must
            # not block attribution forever. A resumed turn extends this bound.
            end = run.get("end", min(now, run.get("last_activity", run["start"]) + 180))
            intervals.append((run["start"], end, identity, run.get("key")))
    intervals.sort()
    credits = [e for e in events if e.get("kind") == "credit"]
    credit_rates = defaultdict(lambda: {"seconds": 0., "delta": 0., "samples": 0})
    baseline = None
    previous = None
    for point in credits:
        if (previous is None or point.get("account") != previous.get("account")
                or point["purchased"] != previous["purchased"]
                or point["spent"] < previous["spent"]):
            baseline = point["remaining"]
            credit_rates.clear()
            previous = point
            continue
        seconds = point["ts"] - previous["ts"]
        overlap = [r for r in intervals if r[0] < point["ts"] and r[1] > previous["ts"]]
        delta = point["spent"] - previous["spent"]
        if not incomplete and 1 <= seconds <= 900 and len(overlap) == 1 and delta >= 0:
            run = overlap[0]
            key = run[3]
            mixed = any(previous["ts"] < e["ts"] <= point["ts"] and
                        (e["model"], e.get("effort", "default")) != key for e in changes)
            if (key and key[0].startswith("openrouter/") and not mixed
                    and run[0] <= previous["ts"] and run[1] >= point["ts"]):
                agg = credit_rates[key]
                agg["seconds"] += seconds
                agg["delta"] += delta
                agg["samples"] += 1
        previous = point
    starts = [r[0] for r in intervals]
    changes_ts = [e["ts"] for e in changes]
    aggregates = defaultdict(lambda: {"seconds": 0., "delta": 0., "samples": 0})
    # Each difference is an ACCOUNT-wide observation. Attribute it only when a
    # single, known turn covers the whole interval and no other turn overlaps.
    grouped = defaultdict(list)
    for event in events:
        if event.get("kind") == "quota":
            grouped[event["bucket"]].append(event)
    if not incomplete:
        for bucket, points in grouped.items():
            for before, after in zip(points, points[1:]):
                seconds = after["ts"] - before["ts"]
                if not 1 <= seconds <= 180 or not before.get("model"):
                    continue
                key = (before["model"], before.get("effort", "default"))
                if key != (after.get("model"), after.get("effort", "default")):
                    continue
                stop = bisect.bisect_left(starts, after["ts"])
                overlap = [r for r in intervals[:stop] if r[1] > before["ts"]]
                if len(overlap) != 1:
                    continue
                run = overlap[0]
                if run[0] > before["ts"] or run[1] < after["ts"] or run[3] != key:
                    continue
                left = bisect.bisect_right(changes_ts, before["ts"])
                right = bisect.bisect_right(changes_ts, after["ts"])
                if any((e["model"], e.get("effort", "default")) != key
                       for e in changes[left:right]):
                    continue
                for slot in ("primary", "secondary"):
                    a, b = before.get(slot), after.get(slot)
                    if not a or not b or a["reset"] != b["reset"] or a["minutes"] != b["minutes"]:
                        continue
                    if after["ts"] >= b["reset"]:
                        continue
                    delta = b["used"] - a["used"]
                    if not 0 <= delta <= 30:
                        continue
                    agg = aggregates[(key, bucket, slot)]
                    agg["seconds"] += seconds
                    agg["delta"] += delta
                    agg["samples"] += 1
    view = {"latest": latest, "models": sorted(models, key=models.get, reverse=True), "usage": dict(usage),
            "aggregates": dict(aggregates), "observed_at": max((e["ts"] for e in latest.values()), default=0),
            "incomplete": incomplete, "credit_latest": previous, "credit_baseline": baseline,
            "credit_rates": dict(credit_rates), "model_seen": models, "intervals": intervals,
            "credit_points": credits}
    if history:
        from history_archive import merge_history
        merge_history(view, history)
    return view


def full_forecast(view, model, bucket, slot="primary"):
    if model[0].startswith("openrouter/"):
        if slot != "primary":
            return {"status": "unavailable"}
        agg = view.get("credit_rates", {}).get(model, {})
        baseline = view.get("credit_baseline")
        if not baseline or baseline <= 0:
            return {"status": "unavailable"}
        minimum_seconds, minimum_samples, minimum_delta = 600, 2, .001
    else:
        agg = view.get("aggregates", {}).get((model, bucket, slot), {})
        baseline = 100
        minimum_seconds, minimum_samples, minimum_delta = 180, 5, 3
    if (agg.get("seconds", 0) < minimum_seconds or agg.get("samples", 0) < minimum_samples
            or agg.get("delta", 0) < minimum_delta):
        return {"status": "learning"}
    rate = agg["delta"] / agg["seconds"] * 3600 / baseline * 100
    return {"status": "ready", "hours": 100 / rate, "rate": rate}


def forecast(view, model, bucket, slot, now=None):
    now = time.time() if now is None else now
    if model[0].startswith("openrouter/"):
        return credit_forecast(view, model, now) if slot == "primary" else {"status": "unavailable"}
    point = view["latest"].get(bucket)
    window = point.get(slot) if point else None
    agg = view["aggregates"].get((model, bucket, slot))
    if not window:
        return {"status": "unavailable"}
    if now >= window["reset"] or now - point["ts"] > 300:
        return {"status": "stale"}
    if window["used"] >= 100:
        return {"status": "exhausted", "hours": 0}
    if not agg or agg["seconds"] < 180 or agg["delta"] < 3 or agg["samples"] < 5:
        return {"status": "learning", "seconds": (agg or {}).get("seconds", 0)}
    rate = agg["delta"] / agg["seconds"] * 3600
    hours = (100 - window["used"]) / rate
    return {"status": "ready", "hours": hours, "rate": rate,
            "reset_first": hours * 3600 >= window["reset"] - now,
            "seconds": agg["seconds"], "samples": agg["samples"],
            "low": hours * agg["delta"] / (agg["delta"] + 1),
            "high": hours * agg["delta"] / max(1, agg["delta"] - 1)}


def credit_forecast(view, model, now):
    point = view.get("credit_latest")
    if not point:
        return {"status": "unavailable"}
    if now - point["ts"] > 600:
        return {"status": "stale"}
    if point["remaining"] <= 0:
        return {"status": "exhausted", "hours": 0}
    agg = view.get("credit_rates", {}).get(model, {})
    if agg.get("seconds", 0) < 600 or agg.get("samples", 0) < 2 or agg.get("delta", 0) < .001:
        return {"status": "learning", "seconds": agg.get("seconds", 0)}
    dollars = agg["delta"] / agg["seconds"] * 3600
    baseline = view["credit_baseline"]
    if not baseline or baseline <= 0:
        return {"status": "unavailable"}
    return {"status": "ready", "hours": point["remaining"] / dollars,
            "rate": dollars / baseline * 100, "dollars": dollars,
            "seconds": agg["seconds"], "samples": agg["samples"], "credit": True}
