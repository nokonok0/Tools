import tempfile
import unittest
from pathlib import Path

from core import build_view, forecast
from last_forecast import LastForecast
from meter_ui import forecast_text, rate_text, full_text, previous_note
from orchestration import orchestration_forecast
from test_core import observations
from tray_display import time_label


class LastForecastTests(unittest.TestCase):
    def test_restarts_and_missing_data_keep_original_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "last.json"
            cache = LastForecast(path)
            value = {"status": "ready", "observed_at": 1200, "rate": 60,
                     "hours": 1, "full_hours": 100 / 60}
            cache.present(("recent", "codex"), value)
            cache.save()
            restored = LastForecast(path)
            for status in ("stale", "learning", "unavailable"):
                shown = restored.present(("recent", "codex"), {"status": status})
                self.assertEqual(shown["hours"], 1)
                self.assertEqual(shown["rate"], 60)
                self.assertEqual(shown["observed_at"], 1200)
                self.assertIn("※", forecast_text(shown))
                self.assertIn("※", rate_text(shown))
                self.assertIn("※", full_text(shown))
                self.assertIn("前回値", previous_note(shown))
                self.assertEqual(time_label(shown), "1h")
            self.assertEqual(restored.present(("other",), {"status": "learning"}),
                             {"status": "learning"})

    def test_exhaustion_keeps_rate_but_never_retains_positive_remaining_time(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = LastForecast(Path(folder) / "last.json")
            cache.present("a", {"status": "ready", "hours": 1, "rate": 60,
                                "full_hours": 100 / 60, "observed_at": 1200})
            shown = cache.present("a", {"status": "exhausted", "hours": 0})
            self.assertEqual(shown["hours"], 0)
            self.assertEqual(shown["rate"], 60)
            self.assertEqual(time_label(shown), "0m")
            self.assertIn("上限到達", forecast_text(shown))

    def test_same_observation_does_not_drift_and_new_observation_replaces_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = LastForecast(Path(folder) / "last.json")
            first = {"status": "ready", "hours": 1, "rate": 60, "observed_at": 1200}
            cache.present("a", first)
            shown = cache.present("a", {**first, "status": "stale", "rate": 70})
            self.assertEqual(shown["rate"], 60)
            shown = cache.present("a", {**first, "observed_at": 1300, "rate": 70})
            self.assertEqual(shown["rate"], 70)
            self.assertFalse(shown.get("historical"))

    def test_stale_and_exhausted_raw_metrics_survive_without_cache(self):
        view = build_view(observations(), now=1241)
        model = ("test-model", "low")
        for result in (forecast(view, model, "codex", "primary", now=10000),
                       orchestration_forecast(view, "codex", now=10000, recent=True)):
            self.assertEqual(result["status"], "stale")
            self.assertEqual(result["rate"], 60)
            self.assertEqual(result["observed_at"], 1240)
        view["latest"]["codex"]["primary"]["used"] = 100
        for result in (forecast(view, model, "codex", "primary", now=1241),
                       orchestration_forecast(view, "codex", now=1241, recent=True)):
            self.assertEqual(result["status"], "exhausted")
            self.assertEqual(result["hours"], 0)
            self.assertEqual(result["rate"], 60)
            self.assertIn("full_hours", result)

    def test_invalid_cache_does_not_break_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "last.json"
            for contents in ("[1]", "broken", '{"a": {"rate": "oops"}}'):
                path.write_text(contents)
                self.assertFalse(LastForecast(path).values)


if __name__ == "__main__":
    unittest.main()
