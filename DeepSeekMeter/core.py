"""Read-only DeepSeek Harness log monitor.

保存するのはモデル名・推論設定・時刻・トークン数・残高などの数値だけで、
会話本文・ツール出力・パス・認証情報は保存しない。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DAY = 86400
MAX_FILES = 64
MAX_COMPRESSED = 8 * 1024 * 1024
MAX_DECOMPRESSED = 64 * 1024 * 1024
BALANCE_FRESH = 600          # 残高の観測を新しいとみなす秒数
SEGMENT_GAP = 900            # 残高の観測間隔の上限
RATE_WINDOW = 3 * 3600       # 消費ペースの学習に使う期間
MIN_ACTIVE = 600             # 学習に必要な活動時間
MIN_SEGMENTS = 2
MIN_SPEND = 0.001

MARKERS = (b"request/header", b"assistant/message", b"turn/start", b"turn/end")


def timestamp(value):
    """DSHのミリ秒時刻と秒時刻の両方を受け付ける。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number / 1000 if number > 1e11 else number


def default_home():
    return Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh")))


def data_directory():
    # Windowsは従来どおりLOCALAPPDATA。LinuxはXDGの保存先を使い、Windowsと混ぜない。
    if os.name != "nt":
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        return Path(base) / "DeepSeekMeter"
    root = os.environ.get("LOCALAPPDATA", str(Path.home() / ".local"))
    return Path(root) / "DeepSeekMeter"


def _token_count(usage, key):
    value = usage.get(key)
    return value if type(value) is int and value >= 0 else 0


def normalize(obj, session, state):
    """1行のJSONから保存対象の観測だけを取り出す。"""
    kind = obj.get("type")
    ts = timestamp(obj.get("time"))
    if ts is None:
        return []
    base = {"ts": ts, "session": session}
    if kind == "request/header":
        data = obj.get("data") or {}
        header = data.get("header") or {}
        config = header.get("config") if isinstance(header, dict) else None
        if not isinstance(config, dict):
            return []
        model = config.get("model")
        if not isinstance(model, str) or not model:
            return []
        effort = str(config.get("reasoningEffort") or "default")[:30]
        state.update(model=model[:120], effort=effort)
        return [{**base, "kind": "context", "model": state["model"], "effort": effort}]
    if kind == "assistant/message":
        data = obj.get("data") or {}
        usage = data.get("usage")
        if not isinstance(usage, dict) or not state.get("model"):
            return []
        record = {
            "input_tokens": _token_count(usage, "inputTokens"),
            "cached_input_tokens": _token_count(usage, "cacheReadTokens"),
            "cache_write_input_tokens": _token_count(usage, "cacheWriteTokens"),
            "output_tokens": _token_count(usage, "outputTokens"),
            "total_tokens": _token_count(usage, "totalTokens"),
        }
        if not any(record.values()):
            return []
        return [{**base, "kind": "usage", "model": state["model"],
                 "effort": state.get("effort", "default"),
                 "turn": str(data.get("turn") or ""), "step": str(data.get("step") or ""),
                 "usage": record}]
    if kind == "turn/start":
        data = obj.get("data") or {}
        turn = str(data.get("turn") or "")
        return [{**base, "kind": "start", "turn": turn}]
    if kind == "turn/end":
        data = obj.get("data") or {}
        turn = str(data.get("turn") or "")
        return [{**base, "kind": "end", "turn": turn}]
    return []


def session_key(path):
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:20]


def _decompress(path):
    if path.suffix != ".zstd":
        data = path.read_bytes()
        if len(data) > MAX_DECOMPRESSED:
            return data[:MAX_DECOMPRESSED], True
        return data, False
    import zstandard
    with path.open("rb") as stream:
        reader = zstandard.ZstdDecompressor().stream_reader(stream, read_across_frames=True)
        chunks, total = [], 0
        while True:
            block = reader.read(512 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_DECOMPRESSED:
                return b"".join(chunks), True
            chunks.append(block)
    return b"".join(chunks), False


class LogReader:
    """DSHのセッションログを読み取り専用で追う。"""

    def __init__(self, home=None):
        self.home = Path(home or default_home())
        self.cache = {}
        self.files = []
        self.discovered_at = 0
        self.truncated = False

    def discover(self, now):
        root = self.home / "sessions"
        if not root.is_dir():
            raise FileNotFoundError("DeepSeek Harnessのsessionsフォルダが見つかりません")
        candidates = []
        paths = list(root.rglob("session.v*.jsonl.zstd")) + list(root.rglob("session.v*.jsonl"))
        for path in paths:
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if modified >= now - 7 * DAY:
                candidates.append((modified, path))
        candidates.sort(reverse=True)
        self.truncated = len(candidates) > MAX_FILES
        self.files = [path for _, path in candidates[:MAX_FILES]]
        self.cache = {path: value for path, value in self.cache.items() if path in self.files}
        self.discovered_at = now

    def poll(self, now=None, force=False):
        now = time.time() if now is None else now
        if force or now - self.discovered_at >= 60:
            self.discover(now)
        events = []
        for path in self.files:
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > (MAX_COMPRESSED if path.suffix == ".zstd" else MAX_DECOMPRESSED):
                self.truncated = True
                continue
            stamp = (stat.st_size, stat.st_mtime_ns)
            cached = self.cache.get(path)
            if cached and cached[0] == stamp:
                continue
            try:
                raw, clipped = _decompress(path)
            except Exception:
                self.truncated = True
                continue
            self.truncated = self.truncated or clipped
            parsed = []
            state = {}
            session = session_key(path)
            for line in raw.split(b"\n"):
                if not line or not any(marker in line for marker in MARKERS):
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                parsed.extend(normalize(obj, session, state))
            self.cache[path] = (stamp, parsed)
            events.extend(parsed)
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
            if raw.get("version") == 2 and isinstance(raw.get("events"), list):
                self.add(raw["events"])
        except (OSError, ValueError, AttributeError):
            pass
        self.dirty = False

    @staticmethod
    def event_key(event):
        kind = event.get("kind")
        if kind == "usage":
            return (event.get("session"), "usage", event.get("turn"), event.get("step"))
        if kind == "balance":
            return ("balance", round(float(event.get("ts", 0)), 3))
        if kind in ("start", "end"):
            return (event.get("session"), kind, event.get("turn"))
        return (event.get("session"), kind, event.get("ts"))

    def add(self, events, now=None):
        now = time.time() if now is None else now
        changed = False
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("ts"), (float, int)):
                continue
            if event["ts"] > now + 60:
                continue
            key = self.event_key(event)
            if key in self.seen:
                continue
            self.seen.add(key)
            self.events.append(event)
            changed = True
        if changed:
            self.events.sort(key=lambda item: item["ts"])
            self.dirty = True
        return changed

    def save(self, force=False):
        now = time.time()
        if not self.dirty or (not force and now - self.saved_at < 120):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 2, "events": self.events},
                                        ensure_ascii=False, separators=(",", ":")),
                             encoding="utf-8")
        temporary.replace(self.path)
        self.saved_at = now
        self.dirty = False


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def overlap_seconds(intervals, start, end):
    total = 0.0
    for left, right in intervals:
        if right <= start:
            continue
        if left >= end:
            break
        total += max(0.0, min(right, end) - max(left, start))
    return total


def build_view(events, now=None, incomplete=False):
    now = time.time() if now is None else now
    events = sorted(events, key=lambda item: item["ts"])
    runs = {}
    usage = defaultdict(lambda: defaultdict(int))
    models = {}
    usage_events = []
    balance_points = []
    for event in events:
        kind = event.get("kind")
        if kind in ("start", "end", "usage", "context"):
            identity = (event.get("session"), event.get("turn"))
            run = runs.setdefault(identity, {})
            run["last_activity"] = event["ts"]
            if kind == "start":
                run["start"] = event["ts"]
            elif kind == "end":
                run["end"] = event["ts"]
        if kind in ("context", "usage") and event.get("model"):
            key = (event["model"], event.get("effort", "default"))
            models[key] = max(models.get(key, 0), event["ts"])
        if kind == "usage":
            key = (event["model"], event.get("effort", "default"))
            for name, value in event["usage"].items():
                usage[key][name] += value
            usage[key]["calls"] += 1
            usage[key]["first"] = min(usage[key].get("first", event["ts"]), event["ts"])
            usage[key]["last"] = max(usage[key].get("last", event["ts"]), event["ts"])
            usage_events.append(event)
        elif kind == "balance":
            balance_points.append(event)
    intervals = []
    for _, run in runs.items():
        if "start" in run:
            end = run.get("end", min(now, run.get("last_activity", run["start"]) + 180))
            intervals.append((run["start"], end))
    intervals = merge_intervals(intervals)
    balance_points.sort(key=lambda item: item["ts"])
    latest = balance_points[-1] if balance_points else None
    spend_24h = sum(max(0.0, a["balance"] - b["balance"])
                    for a, b in zip(balance_points, balance_points[1:])
                    if 0 <= b["ts"] - a["ts"] <= SEGMENT_GAP and b["ts"] >= now - DAY)
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    spend_today = sum(max(0.0, a["balance"] - b["balance"])
                      for a, b in zip(balance_points, balance_points[1:])
                      if 0 <= b["ts"] - a["ts"] <= SEGMENT_GAP and b["ts"] >= midnight)
    return {
        "latest_balance": latest,
        "balance_points": balance_points,
        "usage": dict(usage),
        "usage_events": usage_events,
        "models": sorted(models, key=models.get, reverse=True),
        "intervals": intervals,
        "spend_24h": spend_24h,
        "spend_today": spend_today,
        "observed_at": latest["ts"] if latest else 0,
        "incomplete": incomplete,
    }


def forecast(view, now=None):
    """直近の活動時間あたりの残高消費から、枯渇までの時間を推定する。"""
    now = time.time() if now is None else now
    point = view.get("latest_balance")
    if not point:
        return {"status": "unavailable"}
    balance = max(0.0, float(point.get("balance", 0)))
    stale = now - point["ts"] > BALANCE_FRESH
    if balance <= 0:
        return {"status": "exhausted", "hours": 0, "observed_at": point["ts"],
                "currency": point.get("currency"), "balance": balance}
    points = [item for item in view.get("balance_points", [])
              if item["ts"] >= now - RATE_WINDOW and item["currency"] == point.get("currency")]
    intervals = view.get("intervals", [])
    spend = 0.0
    active = 0.0
    samples = 0
    for before, after in zip(points, points[1:]):
        seconds = after["ts"] - before["ts"]
        if not 0 < seconds <= SEGMENT_GAP:
            continue
        delta = before["balance"] - after["balance"]
        if delta <= 0:
            continue
        covered = overlap_seconds(intervals, before["ts"], after["ts"])
        if covered <= 0:
            continue
        spend += delta
        active += covered
        samples += 1
    if (spend < MIN_SPEND or active < MIN_ACTIVE or samples < MIN_SEGMENTS):
        return {"status": "stale" if stale else "learning", "observed_at": point["ts"],
                "currency": point.get("currency"), "balance": balance,
                "spend": spend, "active": active, "samples": samples}
    rate = spend / active * 3600
    return {"status": "stale" if stale else "ready", "hours": balance / rate, "rate": rate,
            "observed_at": point["ts"], "currency": point.get("currency"), "balance": balance,
            "spend": spend, "active": active, "samples": samples,
            "full_hours": balance / rate}
