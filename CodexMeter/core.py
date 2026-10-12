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
MAX_EVENTS = 24000
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
    if b'"turn_context"' not in line and b'"event_msg"' not in line:
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
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("version") == 1 and isinstance(raw.get("events"), list):
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
            if not now - 7 * DAY <= event["ts"] <= now + 60:
                continue
            key = (event.get("session"), event["ts"], event.get("kind"), event.get("bucket"))
            if key not in self.seen:
                self.seen.add(key)
                self.events.append(event)
                changed = True
        if changed:
            self.events = sorted((e for e in self.events if e["ts"] >= now - 7 * DAY),
                                 key=lambda e: e["ts"])[-MAX_EVENTS:]
            self.seen = {(e.get("session"), e["ts"], e.get("kind"), e.get("bucket"))
                         for e in self.events}
            self.dirty = True
        return changed

    def save(self, force=False):
        now = time.time()
        if not self.dirty or (not force and now - self.saved_at < 120):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "events": self.events},
                                        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.path)
        self.saved_at = now
        self.dirty = False


def build_view(events, now=None, incomplete=False):
    now = time.time() if now is None else now
    events = sorted(events, key=lambda e: e["ts"])
    runs = {}
    latest = {}
    models = {}
    changes = []
    for event in events:
        identity = (event.get("session"), event.get("turn"))
        kind = event.get("kind")
        if event.get("turn"):
            runs.setdefault(identity, {})["last_activity"] = event["ts"]
        if kind in ("context", "quota") and event.get("model"):
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
    intervals = []
    for identity, run in runs.items():
        if "start" in run:
            # Crashed/abandoned turns may have no completion record. They must
            # not block attribution forever. A resumed turn extends this bound.
            end = run.get("end", min(now, run.get("last_activity", run["start"]) + 180))
            intervals.append((run["start"], end, identity, run.get("key")))
    intervals.sort()
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
    return {"latest": latest, "models": sorted(models, key=models.get, reverse=True),
            "aggregates": dict(aggregates), "observed_at": max((e["ts"] for e in latest.values()), default=0),
            "incomplete": incomplete}


def forecast(view, model, bucket, slot, now=None):
    now = time.time() if now is None else now
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
