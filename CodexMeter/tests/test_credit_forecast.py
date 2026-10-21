import unittest
import tempfile
import time
from pathlib import Path

from core import Observations, build_view, forecast, full_forecast

MODEL = ("openrouter/deepseek-v4.1-flash", "low")


def events():
    rows = [{"kind": "context", "ts": 999, "session": "s", "turn": "t",
             "model": MODEL[0], "effort": MODEL[1]},
            {"kind": "start", "ts": 999, "start": 999, "session": "s", "turn": "t"},
            {"kind": "end", "ts": 1601, "session": "s", "turn": "t"}]
    for i in range(3):
        rows.append({"kind": "credit", "session": "credits", "ts": 1000 + i * 300,
                     "account": "a", "purchased": 10, "spent": 5 + i * .1,
                     "remaining": 5 - i * .1})
    return rows


class CreditForecastTests(unittest.TestCase):
    def test_full_duration_uses_stale_history(self):
        view = build_view(events(), 1601)
        self.assertAlmostEqual(full_forecast(view, MODEL, "codex")["hours"], 100 / 24)
        self.assertEqual(forecast(view, MODEL, "codex", "primary", 9999)["status"], "stale")

    def test_full_codex_duration_and_learning(self):
        model = ("gpt-test", "low")
        view = {"aggregates": {(model, "codex", "primary"): {"seconds": 3600, "delta": 20, "samples": 10}}}
        self.assertEqual(full_forecast(view, model, "codex")["hours"], 5)
        self.assertEqual(full_forecast(view, model, "codex", "secondary")["status"], "learning")

    def test_balance_history_survives_restart(self):
        rows = events()
        shift = time.time() - 1601
        rows = [{**e, "ts": e["ts"] + shift,
                 **({"start": e["start"] + shift} if "start" in e else {})} for e in rows]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "observations.json"
            store = Observations(path)
            store.add(rows + rows)
            store.save(force=True)
            loaded = Observations(path)
            result = forecast(build_view(loaded.events), MODEL, "codex", "primary")
            self.assertEqual(result["status"], "ready")
            self.assertAlmostEqual(result["rate"], 24)

    def result(self, rows, now=1601, incomplete=False):
        return forecast(build_view(rows, now, incomplete), MODEL, "codex", "primary", now)

    def test_rate_and_remaining(self):
        result = self.result(events())
        self.assertEqual(result["status"], "ready")
        self.assertAlmostEqual(result["dollars"], 1.2)
        self.assertAlmostEqual(result["rate"], 24)
        self.assertAlmostEqual(result["hours"], 4)

    def test_parallel_and_incomplete(self):
        rows = events() + [{"kind": "start", "session": "other", "turn": "u", "ts": 999},
                           {"kind": "end", "session": "other", "turn": "u", "ts": 1602}]
        self.assertEqual(self.result(rows)["status"], "learning")
        self.assertEqual(self.result(events(), incomplete=True)["status"], "learning")

    def test_topup_account_switch_and_stale(self):
        for field, value in (("purchased", 20), ("account", "b"), ("spent", 1)):
            rows = events()
            rows[-1][field] = value
            self.assertEqual(self.result(rows)["status"], "learning")
        self.assertEqual(self.result(events(), now=2201)["status"], "stale")

    def test_idle_gap_unknown_model_and_model_change(self):
        for extra in ([], [{"kind": "context", "ts": 1200, "session": "s", "turn": "t",
                           "model": "another-model", "effort": "low"}]):
            rows = events() + extra
            if not extra:
                rows = [e for e in rows if e["kind"] != "end"]
            self.assertEqual(self.result(rows)["status"], "learning")
