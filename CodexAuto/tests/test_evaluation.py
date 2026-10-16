import json
from pathlib import Path
import tempfile
import unittest

from codex_auto.evaluation import extract, evaluate
from test_cli import response


class EvaluationTests(unittest.TestCase):
    def test_context_dedup_labels_and_thread_split(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.jsonl"
            entries = [{"type": "response_item", "payload": {"type": "message", "role": role,
                        "content": [{"type": "input_text", "text": text}]}}
                       for role, text in [("user", "# AGENTS.md instructions"),
                                          ("user", "translate hello"), ("assistant", "hello"),
                                          ("user", "continue"), ("user", "continue")]]
            path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
            records = extract([path])
        self.assertEqual(len(records), 2)
        self.assertIsNone(records[0]["expected"])
        self.assertEqual(records[0]["split"], records[1]["split"])
        self.assertEqual(records[1]["context"][-1]["text"], "hello")
        self.assertFalse(evaluate(records, lambda _: response("routine")))
        records[0]["expected"] = "luna"
        self.assertTrue(evaluate(records, lambda _: response("routine"))[0]["correct"])

    def test_failure_is_visible_not_hidden(self):
        def fail(_):
            raise TimeoutError()
        result = evaluate([{"id": "one", "split": "evaluation", "prompt": "hello",
                            "context": [], "expected": "luna"}], fail)[0]
        self.assertEqual(result["source"], "fallback")
        self.assertFalse(result["correct"])
