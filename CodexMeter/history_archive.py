"""Verified history rotation with a small local forecast checkpoint."""
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path

ARCHIVE_DIRECTORY = Path("N:/CodexMeter")
ARCHIVE_THRESHOLD = 100_000_000


def approval_file():
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "CodexMeter" / "archive_destination.json"


def register_archive_destination(destination, approval_path=None):
    """Remember an explicitly approved resolved destination, without copying history."""
    destination = Path(destination)
    if not destination.is_absolute() or not Path(destination.anchor).is_dir():
        raise OSError("退避先は接続済みドライブの絶対パスで指定してください。")
    resolved = destination.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise OSError("退避先にファイルが存在します。フォルダを指定してください。")
    approval_path = approval_file() if approval_path is None else Path(approval_path)
    approval_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = approval_path.with_name(approval_path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump({"version": 1, "destination": str(resolved)}, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(approval_path)
    finally:
        temporary.unlink(missing_ok=True)
    return resolved


def approved_destination(destination, approval_path=None):
    approval_path = approval_file() if approval_path is None else Path(approval_path)
    try:
        saved = json.loads(approval_path.read_text(encoding="utf-8"))
        resolved = destination.resolve()
        if (isinstance(saved, dict) and saved.get("version") == 1
                and saved.get("destination") == str(resolved)):
            return resolved
    except (OSError, ValueError):
        pass
    return None


def pack(mapping):
    return [[list(key), value] for key, value in mapping.items()]


def checkpoint(view, events):
    contexts = {}
    for event in events:
        if event.get("kind") == "context":
            contexts[event["session"]] = event
    return {
        "aggregates": [[list(model), bucket, slot, value]
                       for (model, bucket, slot), value in view["aggregates"].items()],
        "usage": pack(view["usage"]), "models": pack(view["model_seen"]),
        "credit_rates": pack(view["credit_rates"]),
        "credit_latest": view["credit_latest"], "credit_baseline": view["credit_baseline"],
        "latest": view["latest"], "contexts": list(contexts.values()),
        "orchestration": [[[[list(model), count] for model, count in signature], bucket, slot, values]
                          for (signature, bucket, slot), values in view.get("orchestration", {}).get("aggregates", {}).items()],
    }


def add_totals(mapping, key, values):
    target = mapping.setdefault(key, {})
    for name, value in values.items():
        target[name] = target.get(name, 0) + value


def merge_history(view, history):
    for signature, bucket, slot, values in history.get("orchestration", []):
        key = (tuple((tuple(model), count) for model, count in signature), bucket, slot)
        add_totals(view["orchestration"]["aggregates"], key, values)
    for model, bucket, slot, values in history.get("aggregates", []):
        add_totals(view["aggregates"], (tuple(model), bucket, slot), values)
    for model, values in history.get("usage", []):
        add_totals(view["usage"], tuple(model), values)
    for model, stamp in history.get("models", []):
        key = tuple(model)
        view["model_seen"][key] = max(stamp, view["model_seen"].get(key, 0))
    view["models"] = sorted(view["model_seen"], key=view["model_seen"].get, reverse=True)
    for bucket, point in history.get("latest", {}).items():
        view["latest"].setdefault(bucket, point)
    view["observed_at"] = max((p["ts"] for p in view["latest"].values()), default=0)
    old, new = history.get("credit_latest"), view["credit_latest"]
    points = view.get("credit_points", [])
    if old and all(old["account"] == p["account"] and old["purchased"] == p["purchased"]
                   and old["spent"] <= p["spent"] for p in points) and all(
                       a["spent"] <= b["spent"] for a, b in zip(points, points[1:])):
        for model, values in history.get("credit_rates", []):
            add_totals(view["credit_rates"], tuple(model), values)
        view["credit_baseline"] = history["credit_baseline"]
        if not new:
            view["credit_latest"] = old


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.digest()


def archive_on_startup(store, destination=ARCHIVE_DIRECTORY, threshold=ARCHIVE_THRESHOLD, now=None,
                       approval_path=None):
    from core import DAY, build_view
    now = time.time() if now is None else now
    destination = Path(destination)
    try:
        if not store.path.exists() or store.path.stat().st_size <= threshold:
            return "small"
        # Check the drive before mkdir, so a disconnected share is never substituted locally.
        if not Path(destination.anchor).is_dir():
            return "offline"
        if not destination.is_absolute():
            return "untrusted"
        destination = approved_destination(destination, approval_path)
        if destination is None:
            return "untrusted"
        view = build_view(store.events, now, history=store.history)
        cutoff = now - DAY
        while True:
            crossing = [r[0] for r in view["intervals"] if r[0] <= cutoff < r[1]]
            if not crossing:
                break
            cutoff = min(crossing) - .001
        old = [e for e in store.events if e["ts"] <= cutoff]
        if not old:
            return "recent"
        history = checkpoint(build_view(old, now, history=store.history),
                             store.history.get("contexts", []) + old)
        destination.mkdir(parents=True, exist_ok=True)
        store.save(force=True)
        name = time.strftime("observations-%Y%m%d-%H%M%S-", time.localtime(now)) + uuid.uuid4().hex + ".json"
        target = destination / name
        temporary = target.with_suffix(".partial")
        with store.path.open("rb") as source, temporary.open("xb") as output:
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        if digest(store.path) != digest(temporary):
            raise OSError("Archive verification failed")
        temporary.replace(target)
        original = store.events, store.history, store.archived_through, store.seen
        store.events = [e for e in store.events if e["ts"] > cutoff]
        store.history = history
        store.archived_through = cutoff
        store.seen = {store.event_key(e) for e in store.events}
        store.dirty = True
        try:
            store.save(force=True)
        except OSError:
            store.events, store.history, store.archived_through, store.seen = original
            store.dirty = True
            raise
        return "archived"
    except OSError:
        return "failed"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="履歴の退避先を登録します。履歴のコピーは行いません。")
    parser.add_argument("--register", required=True, type=Path, metavar="退避先",
                        help="確認済みの退避先の絶対パス（例: N:\\CodexMeter）")
    args = parser.parse_args()
    try:
        resolved = register_archive_destination(args.register)
    except OSError as exc:
        parser.exit(1, f"登録できませんでした: {exc}\n")
    print(f"退避先を登録しました: {resolved}")
