import unittest

from rate_estimate import record, estimate
from history_archive import add_totals


class RateEstimateTests(unittest.TestCase):
    def test_missing_model_measurement_keeps_learning(self):
        from core import forecast
        view = {"latest": {"codex": {"ts": 1000, "primary":
                {"used": 20, "reset": 9000}}}, "aggregates": {}}
        self.assertEqual(forecast(view, ("unknown", "high"), "codex", "primary",
                                  now=1001)["status"], "learning")

    def test_standard_preserves_lifetime_rate(self):
        agg = self.history()
        self.assertEqual(estimate(agg)["method"], "lifetime")
        self.assertEqual(estimate(agg)["delta"], 100)

    def history(self):
        agg = {"seconds": 0, "delta": 0, "samples": 0}
        for i in range(10):
            record(agg, i * 60, 60, 9)
            record(agg, 86400 + i * 60, 60, 1)
        return agg

    def test_decay_weights_time_and_consumption_equally(self):
        fit = estimate(self.history(), half_life=86400)
        self.assertEqual(fit["method"], "decay")
        # 昨日の90ポイントは半分、今日の10ポイントはそのまま。
        self.assertAlmostEqual(fit["delta"] / fit["seconds"], 55 / 900)

    def test_idle_does_not_change_rate_or_evidence(self):
        agg = self.history()
        shifted = {**agg, "rate_points": [[t + 30 * 86400, s, d]
                                         for t, s, d in agg["rate_points"]]}
        self.assertEqual(estimate(agg, half_life=86400),
                         estimate(shifted, half_life=86400))

    def test_insufficient_effective_evidence_falls_back(self):
        agg = self.history()
        self.assertEqual(estimate(agg, half_life=1)["method"], "lifetime")

    def test_legacy_checkpoint_and_new_points_can_merge(self):
        mapping = {"key": {"seconds": 600, "delta": 9, "samples": 10}}
        add_totals(mapping, "key", self.history())
        self.assertEqual(mapping["key"]["seconds"], 1800)
        self.assertEqual(len(mapping["key"]["rate_points"]), 20)


if __name__ == "__main__":
    unittest.main()
