import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import DAY, Observations, build_view, full_forecast
from history_archive import archive_on_startup, register_archive_destination
from test_core import observations
from test_credit_forecast import events, MODEL


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.approval_path = Path(folder.name) / "approval.json"
        approval = patch("history_archive.approval_file", return_value=self.approval_path)
        approval.start()
        self.addCleanup(approval.stop)

    def test_no_expiry_or_event_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Observations(Path(folder) / "history.json")
            rows = [{"kind": "end", "ts": i, "session": str(i)} for i in range(25000)]
            store.add(rows, now=20 * DAY)
            store.save(force=True)
            self.assertEqual(len(Observations(store.path).events), 25000)

    def test_orchestration_checkpoint_preserves_composition_totals(self):
        from history_archive import checkpoint
        rows = observations() + [
            {"kind": "context", "ts": 999, "session": "child", "turn": "c",
             "model": "gpt-6-luna", "effort": "high"},
            {"kind": "start", "ts": 999, "session": "child", "turn": "c", "start": 999},
            {"kind": "end", "ts": 1250, "session": "child", "turn": "c"}]
        view = build_view(rows, now=1251)
        self.assertTrue(view["orchestration"]["aggregates"])
        self.assertFalse(view["aggregates"])
        saved = json.loads(json.dumps(checkpoint(view, rows)))
        restored = build_view([], now=1251, history=saved)
        self.assertEqual(view["orchestration"]["aggregates"], restored["orchestration"]["aggregates"])
        self.assertFalse(restored["orchestration"]["recent"])

    def rotate(self, rows):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = Observations(Path(folder.name) / "history.json")
        store.add(rows, now=1601)
        store.save(force=True)
        destination = Path(folder.name) / "archive"
        register_archive_destination(destination)
        return store, destination

    def test_unregistered_or_invalid_destination_never_copies_or_prunes(self):
        store, destination = self.rotate(events())
        original = store.path.read_bytes()
        for contents in (None, "not json", "[]", '{"version": 1}',
                         '{"version": 2, "destination": "ignored"}'):
            with self.subTest(contents=contents):
                self.approval_path.unlink(missing_ok=True)
                if contents is not None:
                    self.approval_path.write_text(contents, encoding="utf-8")
                with patch("history_archive.shutil.copyfileobj") as copy:
                    self.assertEqual(archive_on_startup(store, destination, 1, now=3 * DAY), "untrusted")
                    copy.assert_not_called()
                self.assertFalse(destination.exists())
                self.assertEqual(store.path.read_bytes(), original)

    def test_remapped_destination_is_rejected(self):
        store, destination = self.rotate(events())
        original = store.path.read_bytes()
        different_share = destination.parent / "different-share"
        with patch("history_archive.Path.resolve", return_value=different_share):
            self.assertEqual(archive_on_startup(store, destination, 1, now=3 * DAY), "untrusted")
        self.assertFalse(different_share.exists())
        self.assertEqual(store.path.read_bytes(), original)

    def test_approved_resolved_path_is_used_for_copy(self):
        store, destination = self.rotate(events())
        alias = destination.parent / "mapped-drive"
        resolved = destination.resolve()
        with patch("history_archive.Path.resolve", return_value=resolved):
            self.assertEqual(archive_on_startup(store, alias, 1, now=3 * DAY), "archived")
        self.assertFalse(alias.exists())
        self.assertEqual(len(list(destination.glob("*.json"))), 1)

    def test_registration_never_creates_remote_destination_or_copies_history(self):
        destination = self.approval_path.parent / "not-created"
        self.assertEqual(register_archive_destination(destination), destination.resolve())
        self.assertFalse(destination.exists())
        self.assertEqual(json.loads(self.approval_path.read_text())["destination"], str(destination.resolve()))

    def test_relative_registration_is_rejected(self):
        with self.assertRaises(OSError):
            register_archive_destination(Path("archive"))
        self.assertFalse(self.approval_path.exists())

    def test_copy_and_checkpoint_restart_and_reimport(self):
        rows = observations() + [{"kind": "end", "ts": 1241, "session": "one", "turn": "t"}]
        store, destination = self.rotate(rows)
        before = build_view(store.events, now=1242)
        self.assertEqual(archive_on_startup(store, destination, 1, now=3 * DAY), "archived")
        archived = list(destination.glob("*.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(json.loads(archived[0].read_text())["events"], rows)
        loaded = Observations(store.path)
        self.assertFalse(loaded.add(rows, now=3 * DAY))
        after = build_view(loaded.events, history=loaded.history)
        self.assertEqual(before["aggregates"], after["aggregates"])
        self.assertEqual(full_forecast(before, ("test-model", "low"), "codex"),
                         full_forecast(after, ("test-model", "low"), "codex"))
        self.assertEqual(loaded.events, [])

    def test_credit_checkpoint_and_topup(self):
        store, destination = self.rotate(events())
        before = build_view(store.events, now=1601)
        self.assertEqual(archive_on_startup(store, destination, 1, now=3 * DAY), "archived")
        loaded = Observations(store.path)
        after = build_view(loaded.events, history=loaded.history)
        self.assertEqual(full_forecast(before, MODEL, "codex"), full_forecast(after, MODEL, "codex"))
        loaded.add([{"kind": "credit", "ts": 3 * DAY, "session": "credits",
                     "account": "a", "purchased": 20, "spent": 5.2, "remaining": 14.8}], now=3 * DAY)
        self.assertFalse(build_view(loaded.events, history=loaded.history)["credit_rates"])

    def test_repeated_rotation_keeps_recent_turn_and_totals(self):
        first = observations() + [{"kind": "end", "ts": 1241, "session": "one", "turn": "t"}]
        second = [{**e, "ts": e["ts"] + 2 * DAY,
                   **({"start": e["start"] + 2 * DAY} if "start" in e else {}),
                   "turn": "second"} for e in first]
        store, destination = self.rotate(first)
        store.add(second, now=3 * DAY)
        store.save(force=True)
        expected = build_view(store.events, now=3 * DAY)["aggregates"]
        self.assertEqual(archive_on_startup(store, destination, 1, now=3 * DAY), "archived")
        self.assertEqual(build_view(store.events, now=3 * DAY, history=store.history)["aggregates"], expected)
        self.assertTrue(store.events)
        self.assertEqual(archive_on_startup(store, destination, 1, now=5 * DAY), "archived")
        loaded = Observations(store.path)
        self.assertEqual(build_view(loaded.events, history=loaded.history)["aggregates"], expected)

    def test_local_save_failure_preserves_loaded_history(self):
        store, destination = self.rotate(events())
        original = store.path.read_bytes()
        save = store.save
        calls = []
        def fail_second_save(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("local write failed")
            return save(*args, **kwargs)
        with patch.object(store, "save", side_effect=fail_second_save):
            self.assertEqual(archive_on_startup(store, destination, 1, now=3 * DAY), "failed")
        self.assertEqual(store.path.read_bytes(), original)
        self.assertEqual(store.history, {})
        self.assertEqual(store.events, sorted(events(), key=lambda e: e["ts"]))

    def test_offline_small_and_failed_copy_preserve_source(self):
        store, destination = self.rotate(events())
        original = store.path.read_bytes()
        self.assertEqual(archive_on_startup(store, destination, len(original), now=3 * DAY), "small")
        with patch("history_archive.Path.is_dir", return_value=False):
            self.assertEqual(archive_on_startup(store, destination, 1, now=3 * DAY), "offline")
        with patch("history_archive.shutil.copyfileobj", side_effect=OSError):
            self.assertEqual(archive_on_startup(store, destination, 1, now=3 * DAY), "failed")
        self.assertEqual(store.path.read_bytes(), original)
        self.assertEqual(store.history, {})

    def test_verification_failure_never_prunes(self):
        store, destination = self.rotate(events())
        with patch("history_archive.digest", side_effect=[b"a", b"b"]):
            self.assertEqual(archive_on_startup(store, destination, 1, now=3 * DAY), "failed")
        self.assertEqual(len(store.events), len(events()))
        self.assertFalse(list(destination.glob("*.json")))
