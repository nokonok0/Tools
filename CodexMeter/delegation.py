"""Read delegation outcomes without importing or changing the agent's recorder."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
import uuid

PROFILE_NAMES = {"python-feedback-review-v1": "Pythonレビュー",
                 "python-local-records-review-v1": "記録連携レビュー",
                 "markdown-local-links-v1": "Markdown参照点検"}
MIN_COMPARISON = 3


def profile_text(profile):
    return PROFILE_NAMES.get(profile, profile)


def _stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.timestamp()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _read(path):
    with path.open("rb") as handle:
        data = handle.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Record too large")
    value = json.loads(data.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Object required")
    return value


def _run(path):
    raw = _read(path)
    if str(uuid.UUID(raw["id"])) != path.stem:
        raise ValueError("Record identity mismatch")
    for key in ("profile", "model", "procedure_revision"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise ValueError("Missing grouping key")
    if raw["risk"] not in ("low", "medium", "high") or raw["status"] not in ("running", "finished"):
        raise ValueError("Unknown status or risk")
    _stamp(raw["started_at"])
    usage = raw.get("usage", {})
    if not isinstance(usage, dict):
        raise ValueError("Invalid usage")
    for name in ("parent", "child"):
        stream = usage.get(name, {})
        if not isinstance(stream, dict) or not isinstance(stream.get("tokens") or {}, dict):
            raise ValueError("Invalid stream")
    if raw["status"] == "finished":
        if _stamp(raw["ended_at"]) < _stamp(raw["started_at"]):
            raise ValueError("Invalid time order")
        result = raw["result"]
        if result["status"] not in ("pass", "fail", "unverified") or type(result["rework"]) is not bool:
            raise ValueError("Invalid outcome")
        if result.get("cause") not in ("none", "model", "instructions", "skill", "environment", "unknown"):
            raise ValueError("Invalid cause")
    return raw


def _key(run):
    return tuple(run[k] for k in ("profile", "risk", "procedure_revision", "model"))


def _median(values):
    values = [v for v in values if _number(v)]
    return statistics.median(values) if values else None


def _stream(run, name):
    stream = run.get("usage", {}).get(name, {})
    tokens = stream.get("tokens") or {}
    value = tokens.get("total_tokens")
    return value if stream.get("status") == "observed_window" and _number(value) else None


def _total(run):
    usage = run.get("usage", {})
    value = usage.get("observed_total_tokens")
    parent, child = _stream(run, "parent"), _stream(run, "child")
    return value if (usage.get("both_streams_observed") is True and _number(value) and
                     parent is not None and child is not None and value == parent + child) else None


def _summary(runs):
    observed = [r for r in runs if _total(r) is not None]
    return {"count": len(runs), "passes": sum(r["result"]["status"] == "pass" for r in runs),
            "rework": sum(r["result"]["rework"] for r in runs),
            "unverified": sum(r["result"]["status"] == "unverified" for r in runs),
            "observed_count": len(observed), "median_tokens": _median(_total(r) for r in observed),
            "median_parent": _median(_stream(r, "parent") for r in observed),
            "median_child": _median(_stream(r, "child") for r in observed),
            "median_seconds": _median(_stamp(r["ended_at"]) - _stamp(r["started_at"]) for r in runs)}


def _change(before, after):
    return (after / before - 1) * 100 if before and after is not None else None


def load_delegation(directory):
    """Use source records, not a possibly stale policy cache. No writes or network."""
    directory = Path(directory)
    result = {"status": "ready", "groups": [], "comparisons": [], "alerts": [],
              "errors": 0, "pending": 0, "excluded": 0, "count": 0, "passes": 0, "rework": 0}
    if not (directory / "runs").exists():
        result["status"] = "missing"
        return result
    runs = []
    try:
        for path in (directory / "runs").glob("*.json"):
            try:
                runs.append(_run(path))
            except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
                result["errors"] += 1
        runs.sort(key=lambda r: (_stamp(r["started_at"]), r["id"]))
        indexed = {r["id"]: r for r in runs}
        buckets = defaultdict(list)
        for run in runs:
            if run["status"] == "running":
                result["pending"] += 1
            elif run.get("purpose") != "work":
                result["excluded"] += 1
            else:
                buckets[_key(run)].append(run)
                result["count"] += 1
                result["passes"] += run["result"]["status"] == "pass"
                result["rework"] += run["result"]["rework"]
        for key, items in sorted(buckets.items()):
            group = {"key": key, "id": hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16],
                     "profile": key[0], "risk": key[1], "revision": key[2], "model": key[3],
                     **_summary(items), "latest_id": items[-1]["id"], "comparison": None}
            result["groups"].append(group)
            # One notice per consecutive problem streak, not per polling interval.
            streak = []
            for run in reversed(items):
                outcome = run["result"]
                if outcome["cause"] == "environment" or not (outcome["status"] == "fail" or outcome["rework"]):
                    break
                streak.append(run)
            if len(streak) >= 2:
                result["alerts"].append({"key": "problem:" + streak[-1]["id"],
                                         "text": f"委任の失敗・手直しが続いています：{profile_text(key[0])}"})
        for path in (directory / "lessons").glob("*.json"):
            try:
                lesson = _read(path)
                before, after = indexed[lesson["before"]], indexed[lesson["after"]]
                if before.get("purpose") != "work" or after.get("purpose") != "work":
                    continue  # A transparent reclassification invalidates the old comparison.
                if (lesson["status"] != "locally_supported" or lesson["profile"] != before["profile"] or
                    before["profile"] != after["profile"] or before["risk"] != after["risk"] or
                    lesson["risk"] != after["risk"] or lesson["after_model"] != after["model"] or
                    lesson["after_procedure_revision"] != after["procedure_revision"] or
                    before["status"] != "finished" or after["status"] != "finished" or
                    _stamp(before["ended_at"]) >= _stamp(after["started_at"]) or
                    before["result"]["status"] not in ("pass", "fail") or
                    before["result"]["cause"] == "environment" or
                    (before["result"]["status"] == "pass" and not before["result"]["rework"]) or
                    after["result"]["status"] != "pass" or after["result"]["rework"]):
                    raise ValueError("Unsupported comparison")
                if lesson["cause"] == "model":
                    valid = before["model"] != after["model"] and before["procedure_revision"] == after["procedure_revision"]
                else:
                    valid = lesson["cause"] in ("instructions", "skill") and before["model"] == after["model"] and before["procedure_revision"] != after["procedure_revision"]
                if not valid:
                    raise ValueError("More than one changed factor")
                old = _summary([r for r in buckets[_key(before)] if _stamp(r["ended_at"]) <= _stamp(before["ended_at"])][-10:])
                new = _summary([r for r in buckets[_key(after)] if _stamp(r["started_at"]) >= _stamp(after["started_at"])][-10:])
                ready = min(old["count"], new["count"]) >= MIN_COMPARISON
                comparison = {"id": lesson["id"], "created_at": _stamp(lesson["created_at"]),
                              "before": old, "after": new, "ready": ready,
                              "token_change": _change(old["median_tokens"], new["median_tokens"])
                                  if min(old["observed_count"], new["observed_count"]) >= MIN_COMPARISON else None,
                              "time_change": _change(old["median_seconds"], new["median_seconds"]) if ready else None,
                              "pass_change": 100 * (new["passes"] / new["count"] - old["passes"] / old["count"]) if ready else None,
                              "rework_change": 100 * (new["rework"] / new["count"] - old["rework"] / old["count"]) if ready else None}
                result["comparisons"].append(comparison)
                for group in result["groups"]:
                    if group["key"] == _key(after) and (not group["comparison"] or group["comparison"]["created_at"] < comparison["created_at"]):
                        group["comparison"] = comparison
                if ready:
                    result["alerts"].append({"key": "comparison:" + lesson["id"],
                                             "text": f"委任の変更前後を参考比較できます：{profile_text(after['profile'])}"})
            except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, ZeroDivisionError):
                result["errors"] += 1
    except OSError:
        result["errors"] += 1
    if result["errors"]:
        result["status"] = "partial"
        result["alerts"] = []
        result["comparisons"] = []
        for group in result["groups"]:
            group["comparison"] = None
    return result


class DelegationNotices:
    """Persist only notice identifiers; first observation establishes a quiet baseline."""
    def __init__(self, path):
        self.path = Path(path)
        try:
            saved = _read(self.path)
            self.seen = set(saved["seen"])
            if not all(isinstance(key, str) for key in self.seen):
                raise ValueError("Invalid notice IDs")
            self.initialized = saved["initialized"] is True
        except (OSError, ValueError, KeyError, TypeError):
            self.seen, self.initialized = set(), False

    def update(self, snapshot):
        if snapshot["status"] != "ready":
            return []
        fresh = [a for a in snapshot["alerts"] if a["key"] not in self.seen]
        notices = fresh if self.initialized else []
        changed = bool(fresh) or not self.initialized
        self.seen.update(a["key"] for a in fresh)
        self.initialized = True
        if changed:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
                try:
                    temporary.write_text(json.dumps({"initialized": True, "seen": sorted(self.seen)}), encoding="utf-8")
                    temporary.replace(self.path)
                finally:
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return notices
