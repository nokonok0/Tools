import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from meter_ui import Meter


class NotificationTests(unittest.TestCase):
    def test_thresholds_once_per_window(self):
        with tempfile.TemporaryDirectory() as folder:
            notices = []
            meter = SimpleNamespace(tray_available=True, notified=set(),
                                    tray=SimpleNamespace(notify=lambda *args: notices.append(args)),
                                    alert_path=Path(folder) / "notifications.json")
            point = {"ts": 1000, "bucket": "codex", "primary": {"reset": 2000, "used": 81}}
            Meter.check_notifications(meter, point, 1001)
            Meter.check_notifications(meter, point, 1002)
            self.assertEqual(len(notices), 1)
            point["primary"]["used"] = 91
            Meter.check_notifications(meter, point, 1003)
            self.assertEqual(len(notices), 2)
            point["primary"]["reset"] = 3000
            Meter.check_notifications(meter, point, 1004)
            self.assertEqual(len(notices), 3)
            self.assertTrue(meter.alert_path.exists())

    def test_stale_expired_and_tray_unavailable_do_not_notify(self):
        notices = []
        meter = SimpleNamespace(tray_available=True, notified=set(),
                                tray=SimpleNamespace(notify=lambda *args: notices.append(args)))
        point = {"ts": 1000, "bucket": "codex", "primary": {"reset": 2000, "used": 99}}
        Meter.check_notifications(meter, point, 1400)
        point["primary"]["reset"] = 999
        Meter.check_notifications(meter, point, 1001)
        meter.tray_available = False
        point["primary"]["reset"] = 2000
        Meter.check_notifications(meter, point, 1001)
        self.assertEqual(notices, [])
