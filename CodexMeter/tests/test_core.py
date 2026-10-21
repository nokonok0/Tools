import json
import tempfile
import unittest
from pathlib import Path

from core import LogReader, Observations, build_view, forecast, normalize


def observations(other=False, reset_change=False, backward=False):
    events = [{"kind": "context", "ts": 1000, "session": "one", "turn": "t",
               "model": "test-model", "effort": "low"},
              {"kind": "start", "ts": 1000, "session": "one", "turn": "t", "start": 1000}]
    for i in range(9):
        events.append({"kind": "quota", "ts": 1000 + i * 30, "session": "one", "turn": "t",
                       "model": "test-model", "effort": "low", "bucket": "codex",
                       "primary": {"used": 10 + i * .5 if not backward else 10 - i * .5,
                                   "reset": 9000 if not reset_change else 9000 + i,
                                   "minutes": 300}, "secondary": None})
    if other:
        events += [{"kind": "start", "ts": 999, "session": "two", "turn": "u", "start": 999},
                   {"kind": "end", "ts": 1250, "session": "two", "turn": "u", "start": 999}]
    return events


class CoreTests(unittest.TestCase):
    def test_deepseek_usage_without_quota(self):
        state = {"model": "openrouter/deepseek-v4.1-flash", "effort": "low", "turn": "t"}
        body = {"type": "token_usage_record", "timestamp": "2026-10-21T00:00:00Z",
                "payload": {"turn_id": "t", "response_id": "private-id",
                            "usage": {"input_tokens": 100, "cached_input_tokens": 80,
                                      "output_tokens": 20, "total_tokens": 120, "secret": "SECRET"},
                            "thread_token_usage": {"total_tokens": 999999}}}
        rows = normalize(json.dumps(body).encode(), "s", state)
        self.assertEqual(rows[0]["usage"]["total_tokens"], 120)
        self.assertNotIn("private-id", json.dumps(rows))
        self.assertNotIn("SECRET", json.dumps(rows))
        self.assertEqual(normalize(json.dumps(body).encode(), "s", {}), [])
        with tempfile.TemporaryDirectory() as folder:
            store = Observations(Path(folder) / "events.json")
            store.add(rows + rows, rows[0]["ts"])
            view = build_view(store.events, now=rows[0]["ts"])
            model = (state["model"], "low")
            self.assertEqual(view["usage"][model]["total_tokens"], 120)
            self.assertEqual(forecast(view, model, "codex", "primary")["status"], "unavailable")

    def test_single_model_empirical_rate(self):
        view = build_view(observations(), now=1241)
        result = forecast(view, ("test-model", "low"), "codex", "primary", now=1241)
        self.assertEqual(result["status"], "ready")
        self.assertAlmostEqual(result["rate"], 60)
        self.assertAlmostEqual(result["hours"], 86 / 60)

    def test_concurrent_turn_never_attributed(self):
        view = build_view(observations(other=True), now=1241)
        self.assertFalse(view["aggregates"])

    def test_abandoned_old_turn_does_not_block_new_work(self):
        rows = observations() + [{"kind": "start", "ts": 100, "start": 100,
                                  "session": "abandoned", "turn": "old"}]
        result = forecast(build_view(rows, now=1241), ("test-model", "low"),
                          "codex", "primary", now=1241)
        self.assertEqual(result["status"], "ready")

    def test_reset_and_decreasing_counts_excluded(self):
        for changes in ({"reset_change": True}, {"backward": True}):
            view = build_view(observations(**changes), now=1241)
            self.assertFalse(view["aggregates"])

    def test_stale_expired_and_missing(self):
        view = build_view(observations(), now=1241)
        model = ("test-model", "low")
        self.assertEqual(forecast(view, model, "codex", "primary", now=1600)["status"], "stale")
        self.assertEqual(forecast(view, model, "codex", "primary", now=9001)["status"], "stale")
        self.assertEqual(forecast(view, model, "codex", "secondary", now=1241)["status"], "unavailable")

    def test_unknown_and_incomplete_never_estimated(self):
        self.assertFalse(build_view(observations(), now=1241, incomplete=True)["aggregates"])
        rows = observations()
        rows[0]["effort"] = "high"
        self.assertFalse(build_view(rows, now=1241)["aggregates"])

    def test_duplicate_records_and_privacy(self):
        line = json.dumps({"type": "event_msg", "timestamp": "2026-10-13T00:00:00Z",
                           "payload": {"type": "task_complete", "last_agent_message": "SECRET",
                                       "turn_id": "t", "completed_at": "2026-10-13T00:00:00Z"}}).encode()
        event = normalize(line, "s", {})
        self.assertNotIn("SECRET", json.dumps(event))
        with tempfile.TemporaryDirectory() as folder:
            store = Observations(Path(folder) / "events.json")
            now = event[0]["ts"]
            store.add(event + event, now)
            self.assertEqual(len(store.events), 1)

    def test_partial_append_and_read_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            root = home / "sessions"
            root.mkdir()
            p = root / "log.jsonl"
            body = {"type": "turn_context", "timestamp": "2026-10-13T00:00:00Z",
                    "payload": {"model": "test-model", "effort": "low", "turn_id": "t"}}
            text = json.dumps(body).encode()
            p.write_bytes(text[:40])
            reader = LogReader(home)
            self.assertEqual(reader.poll(), [])
            with p.open("ab") as f:
                f.write(text[40:] + b"\n")
            self.assertEqual(len(reader.poll()), 1)
            consumed = reader.total_bytes
            self.assertEqual(reader.poll(), [])
            self.assertEqual(reader.total_bytes, consumed)


if __name__ == "__main__":
    unittest.main()
