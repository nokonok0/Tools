import unittest

from orchestration import build_orchestration, orchestration_forecast


SOL = ("gpt-6.1-sol", "high")
LUNA = ("gpt-6-luna", "medium")


def points():
    return [{"kind": "quota", "ts": 1000 + i * 30, "bucket": "codex",
             "model": SOL[0] if i % 2 else LUNA[0], "effort": "high",
             "primary": {"used": 10 + .5 * i, "reset": 9000, "minutes": 300},
             "secondary": None} for i in range(9)]


def view(events=None, runs=None, changes=(), now=1241, incomplete=False):
    events = points() if events is None else events
    runs = [(999, 2000, ("parent", "p"), SOL),
            (999, 2000, ("child", "c"), LUNA)] if runs is None else runs
    result = build_orchestration(events, runs, changes, now, incomplete)
    return {"orchestration": result, "latest": {"codex": events[-1]}, "incomplete": incomplete}


class OrchestrationTests(unittest.TestCase):
    def test_delayed_counter_rebound_does_not_double_count(self):
        events = points()
        for event, used in zip(events, (10, 11, 10, 12, 12, 13, 13, 14, 14)):
            event["primary"]["used"] = used
        data = view(events=events)
        aggregate = data["orchestration"]["recent"][("codex", "primary")]
        self.assertEqual(aggregate["delta"], 3)

    def test_parallel_composition_counts_account_delta_once(self):
        data = view()
        signature = tuple(sorted(((SOL, 1), (LUNA, 1))))
        self.assertEqual(data["orchestration"]["active"], signature)
        total = data["orchestration"]["aggregates"][(signature, "codex", "primary")]
        self.assertEqual(total, {"seconds": 240, "delta": 4, "samples": 8})
        result = orchestration_forecast(data, "codex", now=1241)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["rate"], 60)
        self.assertAlmostEqual(result["hours"], 86 / 60)
        self.assertAlmostEqual(result["full_hours"], 100 / 60)

    def test_same_model_parallel_count_is_part_of_signature(self):
        runs = [(999, 2000, ("p", "p"), SOL), (999, 2000, ("c", "c"), SOL)]
        data = view(runs=runs)
        self.assertEqual(data["orchestration"]["active"], ((SOL, 2),))
        self.assertEqual(orchestration_forecast(data, "codex", now=1241)["rate"], 60)

    def test_duplicate_reporters_do_not_duplicate_time_or_samples(self):
        events = sorted(points() * 2, key=lambda p: p["ts"])
        data = view(events=events)
        result = orchestration_forecast(data, "codex", now=1241)
        self.assertEqual(result["seconds"], 240)
        self.assertEqual(result["samples"], 8)
        self.assertEqual(result["rate"], 60)

    def test_mid_interval_composition_change_only_learns_recent_total(self):
        runs = [(999, 2000, ("parent", "p"), SOL),
                (999, 1105, ("child", "c"), LUNA),
                (1105, 2000, ("child2", "c2"), LUNA)]
        data = view(runs=runs)
        total = sum(a["seconds"] for a in data["orchestration"]["aggregates"].values())
        self.assertEqual(total, 210)
        self.assertEqual(data["orchestration"]["recent"][("codex", "primary")]["seconds"], 240)

    def test_model_change_resolves_model_at_interval_start(self):
        changes = [{"ts": 999, "session": "p", "turn": "p", "model": SOL[0], "effort": SOL[1]},
                   {"ts": 1105, "session": "p", "turn": "p", "model": LUNA[0], "effort": LUNA[1]}]
        data = view(runs=[(999, 2000, ("p", "p"), LUNA)], changes=changes)
        totals = data["orchestration"]["aggregates"]
        self.assertEqual(totals[(((SOL, 1),), "codex", "primary")]["seconds"], 90)
        self.assertEqual(totals[(((LUNA, 1),), "codex", "primary")]["seconds"], 120)
        self.assertEqual(data["orchestration"]["recent"][("codex", "primary")]["seconds"], 240)

    def test_idle_gaps_are_not_counted(self):
        data = view(runs=[(999, 1105, ("p", "p"), SOL), (1110, 2000, ("c", "c"), LUNA)])
        self.assertEqual(data["orchestration"]["recent"][("codex", "primary")]["seconds"], 210)

    def test_unknown_overlap_rejects_learning(self):
        data = view(runs=[(999, 2000, ("p", "p"), SOL), (999, 2000, ("c", "c"), None)])
        self.assertFalse(data["orchestration"]["aggregates"])
        self.assertFalse(data["orchestration"]["recent"])
        self.assertFalse(data["orchestration"]["active"])

    def test_openrouter_does_not_pollute_codex_composition(self):
        data = view(runs=[(999, 2000, ("p", "p"), SOL),
                          (999, 2000, ("r", "r"), ("openrouter/test", "high"))])
        self.assertEqual(data["orchestration"]["active"], ((SOL, 1),))
        self.assertEqual(orchestration_forecast(data, "codex", now=1241)["rate"], 60)

    def test_provider_switch_does_not_count_openrouter_time(self):
        changes = [{"ts": 999, "session": "p", "turn": "p", "model": "openrouter/test", "effort": "high"},
                   {"ts": 1105, "session": "p", "turn": "p", "model": SOL[0], "effort": SOL[1]}]
        data = view(runs=[(999, 2000, ("p", "p"), SOL)], changes=changes)
        self.assertEqual(data["orchestration"]["recent"][("codex", "primary")]["seconds"], 120)

    def test_conflicting_reported_reset_does_not_bridge_bad_observation(self):
        events = points()
        conflicting = {**events[4], "primary": {**events[4]["primary"], "reset": 9100}}
        events = sorted(events + [conflicting], key=lambda e: e["ts"])
        data = view(events=events)
        self.assertEqual(data["orchestration"]["recent"][("codex", "primary")]["seconds"], 180)

    def test_reset_reversal_invalid_delta_and_expiry_are_rejected(self):
        for mutation in ("reset", "reversal", "jump", "expired"):
            events = points()
            for index, event in enumerate(events):
                if mutation == "reset":
                    event["primary"]["reset"] += index
                elif mutation == "reversal":
                    event["primary"]["used"] = 30 - index
                elif mutation == "jump":
                    event["primary"]["used"] = 31 * index
                else:
                    event["primary"]["reset"] = 900
            data = view(events=events)
            self.assertFalse(data["orchestration"]["aggregates"], mutation)
            self.assertFalse(data["orchestration"]["recent"], mutation)

    def test_recent_window_and_incomplete(self):
        data = view(now=2300)
        self.assertFalse(data["orchestration"]["recent"])
        data = view(incomplete=True)
        self.assertFalse(data["orchestration"]["aggregates"])
        self.assertEqual(orchestration_forecast(data, "codex", now=1241)["status"], "unavailable")

    def test_forecast_states_and_explicit_historical_signature(self):
        data = view()
        self.assertEqual(orchestration_forecast(data, "codex", "secondary", now=1241)["status"], "unavailable")
        self.assertEqual(orchestration_forecast(data, "codex", now=1600)["status"], "stale")
        self.assertEqual(orchestration_forecast(data, "codex", now=9001)["status"], "stale")
        self.assertEqual(orchestration_forecast(data, "codex", signature=(), now=1241)["status"], "learning")
        self.assertEqual(orchestration_forecast(data, "codex", recent=True, now=1241)["status"], "ready")
        signature = data["orchestration"]["active"]
        data["orchestration"]["active"] = ()
        self.assertEqual(orchestration_forecast(data, "codex", signature=signature, now=1241)["status"], "ready")
        data["latest"]["codex"]["primary"]["used"] = 100
        self.assertEqual(orchestration_forecast(data, "codex", now=1241)["status"], "exhausted")


if __name__ == "__main__":
    unittest.main()
