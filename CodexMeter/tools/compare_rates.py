"""保存履歴を読み取り専用で比較。未来の区間を学習側に混ぜない。"""
import json
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import build_view, data_directory
from rate_estimate import estimate


def compare(groups):
    candidates = {"全期間": None, "半減期6時間": 21600, "半減期1日": 86400,
                  "半減期3日": 259200, "半減期7日": 604800}
    errors = {name: [] for name in candidates}
    by_model = {}
    for key, agg in groups.items():
        points = sorted(agg.get("rate_points", []))
        training = {"seconds": 0., "delta": 0., "samples": 0, "rate_points": []}
        block = []
        model_errors = {name: [] for name in candidates}
        for point in points:
            # 別の日や長い休止をまたぐ区間を一つの評価ブロックにしない。
            if block and point[0] - block[-1][0] > 1800:
                for p in block:
                    training["seconds"] += p[1]; training["delta"] += p[2]
                    training["samples"] += 1; training["rate_points"].append(p)
                block = []
            block.append(point)
            seconds = sum(p[1] for p in block)
            if seconds < 600:
                continue
            if training["seconds"] >= 180 and training["delta"] >= 3 and training["samples"] >= 5:
                actual = sum(p[2] for p in block)
                for name, half_life in candidates.items():
                    fit = estimate(training, half_life=half_life) if half_life else training
                    predicted = fit["delta"] / fit["seconds"] * seconds
                    model_errors[name].append(abs(predicted - actual))
                    errors[name].append(abs(predicted - actual))
            for p in block:
                training["seconds"] += p[1]; training["delta"] += p[2]
                training["samples"] += 1; training["rate_points"].append(p)
            block = []
        if model_errors["全期間"]:
            by_model[str(key)] = {name: round(mean(values), 3) for name, values in model_errors.items()}
    return {"blocks": len(errors["全期間"]), "mean_absolute_percentage_point_error":
            {name: round(mean(values), 3) if values else None for name, values in errors.items()},
            "groups": by_model}


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else data_directory() / "observations.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    events = saved.get("events", []) if isinstance(saved, dict) else saved
    view = build_view(events, history=saved.get("history") if isinstance(saved, dict) else None)
    single = {k: v for k, v in view["aggregates"].items() if k[-1] == "primary"}
    combined = {k: v for k, v in view["orchestration"]["aggregates"].items() if k[-1] == "primary"}
    print(json.dumps({"single": compare(single), "composition": compare(combined)}, ensure_ascii=False, indent=2))
