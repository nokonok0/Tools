import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from MeterCommon.runtime_mode import RuntimeMode
from meter_ui import Meter


class SharedModeTests(unittest.TestCase):
    def test_both_instances_follow_either_switch_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mode.json'
            first, second = RuntimeMode(path), RuntimeMode(path)
            self.assertEqual(first.poll_seconds, 10)
            first.toggle()
            self.assertTrue(second.reload())
            self.assertEqual(second.poll_seconds, 30)
            self.assertTrue(RuntimeMode(path).lightweight)
            second.toggle()
            self.assertTrue(first.reload())
            self.assertFalse(first.lightweight)

    def test_corrupt_settings_keep_last_good_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mode.json'
            mode = RuntimeMode(path)
            mode.toggle()
            for content in ('{', '{"lightweight": "false"}', '[]'):
                path.write_text(content, encoding='utf-8')
                mode.reload()
                self.assertTrue(mode.lightweight)

    def test_failed_write_does_not_claim_mode_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mode.json'
            mode = RuntimeMode(path)
            with patch('MeterCommon.runtime_mode.os.replace', side_effect=OSError):
                with self.assertRaises(OSError):
                    mode.toggle()
            self.assertFalse(mode.lightweight)
            self.assertEqual(list(path.parent.iterdir()), [])


class RuntimeIntegrationTests(unittest.TestCase):
    def test_shutdown_between_loop_guard_and_wake_clear_flushes_without_wait(self):
        meter = Meter.__new__(Meter)
        meter.stop = threading.Event()
        meter.runtime_mode = Mock(poll_seconds=30)
        meter.wake = Mock()
        # Reproduce quit arriving just after the loop checked stop.
        meter.wake.clear.side_effect = meter.stop.set
        for name in ('requests', 'messages', 'credit_observations', 'balance_events'):
            setattr(meter, name, queue.Queue())
        meter.reader = Mock(truncated=False)
        meter.reader.poll.return_value = []
        meter.store = Mock(events=[], history={})
        with patch('meter_ui.build_view', return_value={}), \
             patch('meter_ui.archive_on_startup', return_value='unchanged', create=True), \
             patch('meter_ui.load_delegation', return_value={}, create=True):
            meter.run()
        meter.wake.wait.assert_not_called()
        meter.store.save.assert_any_call(force=True)

    def test_manual_refresh_wakes_lightweight_worker_and_flushes_history(self):
        with tempfile.TemporaryDirectory() as directory:
            meter = Meter.__new__(Meter)
            meter.stop = threading.Event()
            meter.init_runtime(RuntimeMode(Path(directory) / 'mode.json'))
            meter.runtime_mode.toggle()
            meter.requests = queue.Queue()
            meter.messages = queue.Queue()
            meter.credit_requests = queue.Queue()
            meter.credit_observations = queue.Queue()
            meter.balance_requests = queue.Queue()
            meter.balance_events = queue.Queue()
            meter.reader = Mock(truncated=False)
            polled = threading.Event()
            calls = []
            def poll(force=False):
                calls.append(force)
                polled.set()
                return []
            meter.reader.poll.side_effect = poll
            meter.store = Mock(events=[], history={})
            with patch('meter_ui.build_view', return_value={}), \
                 patch('meter_ui.archive_on_startup', return_value='unchanged', create=True), \
                 patch('meter_ui.load_delegation', return_value={}, create=True):
                worker = threading.Thread(target=meter.run)
                worker.start()
                try:
                    self.assertTrue(polled.wait(2))
                    polled.clear()
                    meter.request_refresh()
                    self.assertTrue(polled.wait(2), 'manual refresh waited for the 30s timer')
                    self.assertTrue(calls[-1])
                finally:
                    meter.stop.set()
                    meter.wake.set()
                    worker.join(3)
            self.assertFalse(worker.is_alive())
            meter.store.save.assert_any_call(force=True)


if __name__ == '__main__':
    unittest.main()
