import json
import tempfile
import time
import unittest
from pathlib import Path

import core


def header(model="deepseek-flash", effort="low", ts=1_793_632_559_926):
    return {"type": "request/header", "seq": 1, "time": ts,
            "data": {"header": {"config": {"provider": "deepseek-account", "model": model,
                                           "reasoningEffort": effort, "maxTokens": 256000}}}}


def message(turn=1, step=1, ts=1_793_632_560_000, **usage):
    return {"type": "assistant/message", "seq": 2, "time": ts,
            "data": {"turn": turn, "step": step, "usage": usage}}


class NormalizeTests(unittest.TestCase):
    def test_header_then_usage_keeps_numbers_only(self):
        state = {}
        context = core.normalize(header(), "s", state)
        self.assertEqual(context[0]["kind"], "context")
        self.assertEqual(context[0]["model"], "deepseek-flash")
        self.assertEqual(state["effort"], "low")
        events = core.normalize(message(inputTokens=10, cacheReadTokens=20, outputTokens=5,
                                        totalTokens=35, content="会話本文"), "s", state)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["kind"], "usage")
        self.assertEqual(event["usage"]["input_tokens"], 10)
        self.assertEqual(event["usage"]["cached_input_tokens"], 20)
        self.assertEqual(event["usage"]["output_tokens"], 5)
        self.assertEqual(event["model"], "deepseek-flash")
        self.assertNotIn("text", event)

    def test_usage_without_header_is_ignored(self):
        self.assertEqual(core.normalize(message(inputTokens=1), "s", {}), [])

    def test_timestamp_accepts_milliseconds(self):
        self.assertAlmostEqual(core.timestamp(1_793_632_559_926), 1_793_632_559.926, places=3)
        self.assertEqual(core.timestamp(None), None)

    def test_turn_boundaries(self):
        start = core.normalize({"type": "turn/start", "time": 1_000, "data": {"turn": 2}}, "s", {})
        end = core.normalize({"type": "turn/end", "time": 2_000, "data": {"turn": 2}}, "s", {})
        self.assertEqual(start[0]["kind"], "start")
        self.assertEqual(end[0]["turn"], "2")


class LogReaderTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        folder = self.home / "sessions" / "workspace" / "session-1"
        folder.mkdir(parents=True)
        self.path = folder / "session.v4.jsonl.zstd"
        self.write([{"type": "session", "id": "s"},
                    header(ts=1_700_000_000_000),
                    message(ts=1_700_000_005_000, inputTokens=100, outputTokens=10,
                            totalTokens=110)])

    def write(self, rows):
        import zstandard
        payload = "\n".join(json.dumps(row) for row in rows) + "\n"
        self.path.write_bytes(zstandard.ZstdCompressor().compress(payload.encode("utf-8")))

    def test_reads_and_caches(self):
        reader = core.LogReader(self.home)
        now = time.time()
        events = reader.poll(now=now, force=True)
        self.assertEqual([event["kind"] for event in events], ["context", "usage"])
        self.assertEqual(reader.poll(now=now), [])

    def test_missing_folder_raises(self):
        reader = core.LogReader(self.home / "missing")
        with self.assertRaises(FileNotFoundError):
            reader.poll(now=time.time(), force=True)

    def test_reads_plaintext_jsonl(self):
        rows = [{"type": "session", "id": "s"}, header(ts=1_700_000_000_000),
                message(ts=1_700_000_005_000, inputTokens=7, outputTokens=3, totalTokens=10)]
        path = self.path.parent / "session.v4.jsonl"
        self.path.unlink()
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        events = core.LogReader(self.home).poll(now=time.time(), force=True)
        self.assertEqual([event["kind"] for event in events], ["context", "usage"])
        self.assertEqual(events[1]["usage"]["input_tokens"], 7)


class ObservationsTests(unittest.TestCase):
    def test_deduplicates_usage(self):
        path = Path(tempfile.mkdtemp()) / "observations.json"
        store = core.Observations(path)
        event = {"kind": "usage", "session": "s", "turn": "1", "step": "1", "ts": 10.0,
                 "model": "deepseek-flash", "effort": "low", "usage": {"total_tokens": 5}}
        self.assertTrue(store.add([event], now=100))
        self.assertFalse(store.add([event], now=100))
        self.assertEqual(len(store.events), 1)
        store.save(force=True)
        self.assertEqual(len(core.Observations(path).events), 1)


class ViewTests(unittest.TestCase):
    def test_totals_and_forecast(self):
        now = 1_700_010_000.0
        t0 = now - 1200
        events = [
            {"kind": "start", "ts": t0, "session": "s", "turn": "1"},
            {"kind": "context", "ts": t0, "session": "s", "model": "deepseek-flash", "effort": "low"},
            {"kind": "usage", "ts": t0 + 60, "session": "s", "turn": "1", "step": "1",
             "model": "deepseek-flash", "effort": "low",
             "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10,
                       "total_tokens": 130, "cache_write_input_tokens": 0}},
            {"kind": "usage", "ts": t0 + 120, "session": "s", "turn": "1", "step": "2",
             "model": "deepseek-flash", "effort": "low",
             "usage": {"input_tokens": 50, "cached_input_tokens": 40, "output_tokens": 5,
                       "total_tokens": 95, "cache_write_input_tokens": 0}},
            {"kind": "end", "ts": t0 + 600, "session": "s", "turn": "1"},
            {"kind": "balance", "ts": t0, "session": "b", "currency": "USD", "balance": 5.0,
             "total_cost": 0.1},
            {"kind": "balance", "ts": t0 + 300, "session": "b", "currency": "USD", "balance": 4.9},
            {"kind": "balance", "ts": t0 + 600, "session": "b", "currency": "USD", "balance": 4.8},
        ]
        view = core.build_view(events, now)
        usage = view["usage"][("deepseek-flash", "low")]
        self.assertEqual(usage["input_tokens"], 150)
        self.assertEqual(usage["cached_input_tokens"], 60)
        self.assertEqual(usage["output_tokens"], 15)
        self.assertEqual(usage["calls"], 2)
        result = core.forecast(view, now)
        self.assertEqual(result["status"], "ready")
        self.assertAlmostEqual(result["rate"], 1.2, places=6)
        self.assertAlmostEqual(result["hours"], 4.0, places=6)
        self.assertAlmostEqual(view["spend_24h"], 0.2, places=6)

    def test_learning_without_activity(self):
        now = 1_700_010_000.0
        events = [
            {"kind": "balance", "ts": now - 600, "session": "b", "currency": "USD", "balance": 5.0},
            {"kind": "balance", "ts": now - 300, "session": "b", "currency": "USD", "balance": 4.9},
        ]
        result = core.forecast(core.build_view(events, now), now)
        self.assertEqual(result["status"], "learning")

    def test_stale_marker(self):
        now = 1_700_010_000.0
        events = [{"kind": "balance", "ts": now - 700, "session": "b", "currency": "USD",
                   "balance": 5.0}]
        result = core.forecast(core.build_view(events, now), now)
        self.assertEqual(result["status"], "stale")

    def test_empty(self):
        self.assertEqual(core.forecast(core.build_view([], 1_700_000_000))["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
