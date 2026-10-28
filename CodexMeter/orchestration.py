"""Account-wide rates, kept separate from single-model attribution."""
from collections import Counter, defaultdict
import bisect
import time

RECENT_SECONDS = 15 * 60


def _aggregate():
    return {"seconds": 0., "delta": 0., "samples": 0}


def _signature(keys):
    return tuple(sorted(Counter(keys).items()))


def build_orchestration(events, intervals, changes, now, incomplete=False):
    """Measure quota once per account interval, never once per reporting agent."""
    contexts = defaultdict(list)
    for change in sorted(changes, key=lambda e: e["ts"]):
        contexts[(change.get("session"), change.get("turn"))].append(change)
    context_stamps = {identity: [r["ts"] for r in records] for identity, records in contexts.items()}

    def key_at(run, stamp):
        records = contexts.get(run[2], [])
        if not records:
            return run[3]
        index = bisect.bisect_right(context_stamps[run[2]], stamp) - 1
        if index < 0:
            return None
        record = records[index]
        return (record["model"], record.get("effort", "default"))

    # Resolve changes inside a turn before measuring the union. In particular,
    # a provider change must not make time on OpenRouter look like Codex time.
    segments = []
    for run in intervals:
        boundaries = [run[0]]
        last_key = key_at(run, run[0])
        for change in contexts.get(run[2], []):
            if not run[0] < change["ts"] < run[1]:
                continue
            key = (change["model"], change.get("effort", "default"))
            if key != last_key:
                boundaries.append(change["ts"])
                last_key = key
        boundaries.append(run[1])
        for start, end in zip(boundaries, boundaries[1:]):
            segments.append((start, end, run[2], key_at(run, start)))

    def codex_runs(start, end):
        overlapping = [r for r in segments if r[0] < end and r[1] > start]
        relevant = []
        for run in overlapping:
            key = run[3]
            if key is None:
                return None
            if not key[0].startswith("openrouter/"):
                relevant.append((run, key))
        return relevant

    active_keys = [key_at(r, now) for r in intervals if r[0] <= now <= r[1]]
    active = () if incomplete or any(k is None for k in active_keys) else _signature(
        k for k in active_keys if not k[0].startswith("openrouter/"))
    result = {"aggregates": {}, "recent": {}, "active": active}
    if incomplete:
        return result

    grouped = defaultdict(lambda: defaultdict(list))
    for event in events:
        if event.get("kind") == "quota" and event["ts"] <= now:
            grouped[event["bucket"]][event["ts"]].append(event)

    totals = defaultdict(_aggregate)
    recent = defaultdict(_aggregate)
    for bucket, stamps in grouped.items():
        points = []
        for stamp, records in sorted(stamps.items()):
            point = {"ts": stamp}
            for slot in ("primary", "secondary"):
                windows = [r[slot] for r in records if r.get(slot)]
                # Different reporters can observe a rounded counter differently.
                # A conflicting window identity is not a usable observation.
                identities = {(w["reset"], w["minutes"]) for w in windows}
                point[slot] = max(windows, key=lambda w: w["used"]) if len(identities) == 1 else None
            points.append(point)
        high_water = {}
        for before, after in zip(points, points[1:]):
            start, end = before["ts"], after["ts"]
            seconds = end - start
            if not 1 <= seconds <= 180:
                continue
            relevant = codex_runs(start, end)
            if not relevant:
                continue
            # The union must cover the entire wall-clock interval; idle time is
            # not evidence of a slower consumption rate.
            covered = start
            for run, _ in sorted(relevant, key=lambda r: r[0][0]):
                if run[0] > covered:
                    break
                covered = max(covered, run[1])
            if covered < end:
                continue
            stable = all(run[0] <= start and run[1] >= end for run, _ in relevant)
            signature = _signature(key for _, key in relevant) if stable else ()
            for slot in ("primary", "secondary"):
                a, b = before[slot], after[slot]
                if (not a or not b or a["reset"] != b["reset"]
                        or a["minutes"] != b["minutes"] or end >= b["reset"]):
                    continue
                delta = b["used"] - a["used"]
                identity = (slot, b["reset"], b["minutes"])
                maximum = high_water.get(identity, a["used"])
                high_water[identity] = max(maximum, a["used"], b["used"])
                # A delayed reporter can repeat an older account counter. Do
                # not count the rebound from that old value as fresh usage.
                if a["used"] < maximum or b["used"] < maximum:
                    continue
                if not 0 <= delta <= 30:
                    continue
                selected = []
                if signature:
                    selected.append(totals[(signature, bucket, slot)])
                if start >= now - RECENT_SECONDS:
                    selected.append(recent[(bucket, slot)])
                for aggregate in selected:
                    aggregate["seconds"] += seconds
                    aggregate["delta"] += delta
                    aggregate["samples"] += 1
    result.update(aggregates=dict(totals), recent=dict(recent))
    return result


def orchestration_forecast(view, bucket, slot="primary", now=None, signature=None, recent=False):
    now = time.time() if now is None else now
    if view.get("incomplete"):
        return {"status": "unavailable"}
    point = view.get("latest", {}).get(bucket)
    window = point.get(slot) if point else None
    if not window:
        return {"status": "unavailable"}
    if now >= window["reset"] or now - point["ts"] > 300:
        return {"status": "stale"}
    if window["used"] >= 100:
        return {"status": "exhausted", "hours": 0}
    orchestration = view.get("orchestration", {})
    if recent:
        aggregate = orchestration.get("recent", {}).get((bucket, slot), {})
    else:
        signature = orchestration.get("active", ()) if signature is None else signature
        aggregate = orchestration.get("aggregates", {}).get((signature, bucket, slot), {})
    seconds = aggregate.get("seconds", 0)
    if seconds < 180 or aggregate.get("samples", 0) < 5 or aggregate.get("delta", 0) < 3:
        return {"status": "learning", "seconds": seconds, "samples": aggregate.get("samples", 0)}
    rate = aggregate["delta"] / seconds * 3600
    hours = (100 - window["used"]) / rate
    return {"status": "ready", "hours": hours, "rate": rate, "full_hours": 100 / rate,
            "seconds": seconds, "samples": aggregate["samples"],
            "reset_first": hours * 3600 >= window["reset"] - now}
