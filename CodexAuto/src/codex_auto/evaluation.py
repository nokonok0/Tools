"""実チャットを公開せずに判定用の教材と評価結果を保存する。"""

import argparse
from collections import Counter, deque
import hashlib
import json
import os
from pathlib import Path

from .cli import Ollaya, RoutingError, classify, codex_home


def private_dir():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CodexAuto" / "evaluation"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract(files, limit=240):
    records = []
    seen = set()
    for path in files:
        start = len(records)
        context = deque(maxlen=4)
        thread = path.stem
        split = "evaluation" if int(digest(thread)[:8], 16) % 5 == 0 else "calibration"
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                payload = entry.get("payload", {})
                if entry.get("type") != "response_item" or payload.get("type") != "message":
                    continue
                role = payload.get("role")
                if role not in ("user", "assistant"):
                    continue
                text = "\n".join(item.get("text", "") for item in payload.get("content", [])
                                 if isinstance(item, dict) and item.get("type") in ("input_text", "output_text", "text")).strip()
                if not text:
                    continue
                if role == "user":
                    # 起動時の指示と添付の機械生成情報は教材に混ぜない。
                    if text.startswith(("<environment_context>", "# AGENTS.md", "<INSTRUCTIONS>",
                                        "<permissions", "<user_action>", "<send_user_message")):
                        continue
                    key = digest(" ".join(text.split()))
                    if key not in seen and len(text) <= 4000:
                        seen.add(key)
                        records.append({"id": digest(thread + key)[:20], "thread": digest(thread)[:16],
                                        "split": split, "timestamp": entry.get("timestamp"),
                                        "prompt": text, "context": list(context),
                                        "expected": None, "rationale": "", "label_source": None})
                context.append({"role": role, "text": text[:300]})
        records[start:] = records[start:][-20:]
        if len(records) >= limit:
            break
    return records[:limit]


def evaluate(records, decide):
    results = []
    for record in records:
        if record.get("expected") not in ("luna", "sol"):
            continue
        try:
            kind, confidence, reason, source = classify(record["prompt"], decide,
                [(item["role"], item["text"]) for item in record["context"]])
            selected = "luna" if kind == "routine" else "sol"
        except (RoutingError, OSError, ValueError, KeyError, TypeError, AttributeError):
            selected, confidence, reason, source = "sol", None, "判定失敗", "fallback"
        results.append({"id": record["id"], "split": record["split"],
                        "expected": record["expected"], "selected": selected,
                        "correct": selected == record["expected"], "confidence": confidence,
                        "source": source, "reason": reason})
    return results


def main():
    parser = argparse.ArgumentParser(description="ローカル専用の振り分け教材・評価")
    parser.add_argument("action", choices=("extract", "evaluate"))
    parser.add_argument("--limit", type=int, default=240)
    parser.add_argument("--split", choices=("calibration", "evaluation"), default="calibration")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("件数は1〜1000にしてください")
    folder = private_dir()
    folder.mkdir(parents=True, exist_ok=True)
    dataset = folder / "examples.json"
    if args.action == "extract":
        if dataset.exists():
            parser.error("教材が既にあります。既存のラベルを保護するため上書きしません")
        files = sorted((codex_home() / "sessions").rglob("*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:30]
        records = extract(files, args.limit)
        dataset.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"count": len(records), "splits": dict(Counter(r["split"] for r in records))}))
    else:
        labels = json.loads((folder / "labels.json").read_text(encoding="utf-8"))
        records = json.loads(dataset.read_text(encoding="utf-8"))
        for record in records:
            record.update(labels.get(record["id"], {}))
        records = [r for r in records
                   if r["split"] == args.split and r.get("expected") in ("luna", "sol")][:args.limit]
        results = evaluate(records, Ollaya().decide)
        (folder / f"results-{args.split}.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"count": len(results), "correct": sum(r["correct"] for r in results),
                          "confusion": dict(Counter(f"{r['expected']}->{r['selected']}" for r in results))}))


if __name__ == "__main__":
    main()
