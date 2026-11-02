import json
from pathlib import Path
import tempfile
import unittest
import uuid

from delegation import load_delegation, DelegationNotices
from meter_ui import Meter


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "runs").mkdir()
        self.sequence = 0

    def save(self, folder, record):
        path = self.root / folder / (record["id"] + ".json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
        return record

    def run_record(self, **changes):
        self.sequence += 1
        minute = self.sequence
        record = {"id": str(uuid.uuid4()), "profile": "python-feedback-review-v1", "risk": "low",
                  "procedure_revision": "v1", "model": "gpt-6-luna:high", "purpose": "work",
                  "status": "finished", "started_at": f"2026-11-02T00:{minute:02d}:00+00:00",
                  "ended_at": f"2026-11-02T00:{minute:02d}:20+00:00",
                  "result": {"status": "pass", "rework": False, "cause": "none"},
                  "usage": {"both_streams_observed": True, "observed_total_tokens": 120,
                            "parent": {"status": "observed_window", "tokens": {"total_tokens": 100}},
                            "child": {"status": "observed_window", "tokens": {"total_tokens": 20}}}}
        record.update(changes)
        return self.save("runs", record)

    def lesson(self, before, after, **changes):
        value = {"id": str(uuid.uuid4()), "created_at": "2026-11-02T01:00:00+00:00",
                 "profile": before["profile"], "risk": after["risk"], "before": before["id"],
                 "after": after["id"], "after_model": after["model"],
                 "after_procedure_revision": after["procedure_revision"], "cause": "skill",
                 "status": "locally_supported"}
        value.update(changes)
        return self.save("lessons", value)

    def problem(self, rework=False, cause="model"):
        return {"status": "pass" if rework else "fail", "rework": rework, "cause": cause}

    def test_empty_and_missing_are_distinct(self):
        self.assertEqual(load_delegation(self.root)["status"], "ready")
        self.assertEqual(load_delegation(self.root / "missing")["status"], "missing")

    def test_reads_source_and_excludes_setup_evaluation_and_legacy(self):
        self.run_record()
        for purpose in ("setup", "evaluation", None):
            self.run_record(purpose=purpose)
        self.run_record(status="running")
        (self.root / "policy.json").write_text('{"groups": []}', encoding="utf-8")
        view = load_delegation(self.root)
        self.assertEqual((view["count"], view["passes"], view["excluded"], view["pending"]), (1, 1, 3, 1))
        self.assertEqual(view["groups"][0]["median_tokens"], 120)

    def test_missing_or_partial_usage_does_not_mean_zero(self):
        self.run_record(usage={})
        self.run_record(usage={"both_streams_observed": False, "observed_total_tokens": 100})
        group = load_delegation(self.root)["groups"][0]
        self.assertIsNone(group["median_tokens"])
        self.assertIsNone(group["median_parent"])
        self.assertEqual(group["observed_count"], 0)

    def test_grouping_preserves_risk_revision_and_model(self):
        self.run_record()
        self.run_record(risk="high")
        self.run_record(procedure_revision="v2")
        self.run_record(model="gpt-6.1-sol:medium")
        self.assertEqual(len(load_delegation(self.root)["groups"]), 4)

    def test_inconsistent_observation_flags_or_totals_are_not_counted(self):
        self.run_record(usage={"both_streams_observed": True, "observed_total_tokens": 120,
                               "parent": {"status": "missing", "tokens": {}},
                               "child": {"status": "missing", "tokens": {}}})
        valid = self.run_record()
        valid["usage"]["observed_total_tokens"] = 999
        self.save("runs", valid)
        group = load_delegation(self.root)["groups"][0]
        self.assertEqual(group["observed_count"], 0)
        self.assertIsNone(group["median_tokens"])

    def test_corrupt_record_and_malformed_usage_are_visible(self):
        self.run_record()
        self.run_record(usage=[])
        (self.root / "runs" / "broken.json").write_text("{", encoding="utf-8")
        view = load_delegation(self.root)
        self.assertEqual((view["status"], view["errors"], view["count"]), ("partial", 2, 1))
        self.assertEqual(view["alerts"], [])

    def test_repeated_problem_notice_is_once_per_streak(self):
        self.run_record(result=self.problem())
        self.assertEqual(load_delegation(self.root)["alerts"], [])
        self.run_record(result=self.problem(rework=True))
        first = load_delegation(self.root)["alerts"][0]["key"]
        self.run_record(result=self.problem())
        self.assertEqual(load_delegation(self.root)["alerts"][0]["key"], first)
        self.run_record()
        self.assertEqual(load_delegation(self.root)["alerts"], [])
        self.run_record(result=self.problem())
        self.run_record(result=self.problem())
        self.assertNotEqual(load_delegation(self.root)["alerts"][0]["key"], first)

    def test_environment_failure_does_not_trigger_model_problem_notice(self):
        for _ in range(3):
            self.run_record(result=self.problem(cause="environment"))
        self.assertEqual(load_delegation(self.root)["alerts"], [])

    def test_single_improvement_pair_is_not_a_percentage_claim(self):
        before = self.run_record(result=self.problem())
        after = self.run_record(procedure_revision="v2")
        self.lesson(before, after)
        comparison = load_delegation(self.root)["comparisons"][0]
        self.assertFalse(comparison["ready"])
        self.assertIsNone(comparison["token_change"])
        self.assertIsNone(comparison["pass_change"])

    def populated_comparison(self):
        for _ in range(3):
            before = self.run_record(result=self.problem())
        after = self.run_record(procedure_revision="v2")
        for _ in range(2):
            self.run_record(procedure_revision="v2")
        lesson = self.lesson(before, after)
        return before, after, lesson

    def test_verified_change_compares_only_correct_groups_and_periods(self):
        self.populated_comparison()
        self.run_record(procedure_revision="v2", risk="high", result=self.problem())
        view = load_delegation(self.root)
        comparison = view["comparisons"][0]
        self.assertTrue(comparison["ready"])
        self.assertEqual(comparison["pass_change"], 100)
        self.assertEqual(comparison["token_change"], 0)
        self.assertEqual(comparison["before"]["count"], 3)
        self.assertEqual(comparison["after"]["count"], 3)
        self.assertTrue(any(a["key"].startswith("comparison:") for a in view["alerts"]))

    def test_reclassification_removes_stale_comparison(self):
        before, _, _ = self.populated_comparison()
        before["purpose"] = "setup"
        self.save("runs", before)
        view = load_delegation(self.root)
        self.assertEqual(view["status"], "ready")
        self.assertEqual(view["comparisons"], [])

    def test_invalid_or_incomplete_evidence_suppresses_comparison_and_notices(self):
        before, after, lesson = self.populated_comparison()
        lesson["after_model"] = "wrong:high"
        self.save("lessons", lesson)
        view = load_delegation(self.root)
        self.assertEqual(view["status"], "partial")
        self.assertEqual(view["comparisons"], [])
        self.assertEqual(view["alerts"], [])

    def test_summary_text_explains_observation_not_billing(self):
        self.run_record()
        group = load_delegation(self.root)["groups"][0]
        detail = Meter.delegation_detail(None, group)
        self.assertIn("親 100／子 20", detail)
        self.assertIn("課金額ではありません", detail)
        self.assertIn("記録不足", detail)

    def test_notices_baseline_persistence_and_new_events(self):
        path = self.root / "appdata" / "notices.json"
        notices = DelegationNotices(path)
        view = {"status": "ready", "alerts": [{"key": "old", "text": "old"}]}
        self.assertEqual(notices.update(view), [])
        view["alerts"].append({"key": "new", "text": "new"})
        self.assertEqual(notices.update(view), [{"key": "new", "text": "new"}])
        self.assertEqual(DelegationNotices(path).update(view), [])
        view["status"] = "partial"
        view["alerts"].append({"key": "not-yet", "text": "partial"})
        self.assertEqual(notices.update(view), [])
        view["status"] = "ready"
        self.assertEqual(len(notices.update(view)), 1)

    def test_reading_records_does_not_modify_them_or_policy(self):
        self.run_record()
        (self.root / "policy.json").write_text("{}", encoding="utf-8")
        before = {str(p): p.read_bytes() for p in self.root.rglob("*.json")}
        load_delegation(self.root)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*.json")})


if __name__ == "__main__":
    unittest.main()
