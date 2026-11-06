"""活動中の有効区間を、最後の実測からの経過日数で重み付けする。"""
# 実履歴比較では改善を確認できなかったため、標準は全期間平均を維持する。
# 候補比較ではhalf_lifeを明示して重み付きの式を評価する。
HALF_LIFE = None


def record(aggregate, stamp, seconds, delta):
    aggregate["seconds"] += seconds
    aggregate["delta"] += delta
    aggregate["samples"] += 1
    aggregate.setdefault("rate_points", []).append([stamp, seconds, delta])


def estimate(aggregate, minimum_seconds=180, minimum_samples=5, minimum_delta=3,
             half_life=HALF_LIFE):
    points = aggregate.get("rate_points", [])
    if points and half_life is not None:
        latest = max(p[0] for p in points)
        seconds = delta = samples = 0.0
        for stamp, duration, used in points:
            weight = 2 ** (-(latest - stamp) / half_life)
            seconds += weight * duration
            delta += weight * used
            samples += weight
        if seconds >= minimum_seconds and samples >= minimum_samples and delta >= minimum_delta:
            return {"seconds": seconds, "delta": delta, "samples": samples,
                    "method": "decay", "half_life": half_life}
    # 古い退避済み集計には日時がない。十分な最近の実測が集まるまでの補助に使う。
    return {**aggregate, "method": "lifetime"}
