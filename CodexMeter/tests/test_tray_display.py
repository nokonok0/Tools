import unittest

from tray_display import make_icon, time_label


class TrayDisplayTests(unittest.TestCase):
    def test_times(self):
        for hours, label in [(0, "1m"), (0.75, "45m"), (1, "1h"),
                             (2.5, "2.5h"), (12.2, "13h"), (120, "99+")]:
            self.assertEqual(time_label({"status": "ready", "hours": hours}), label)

    def test_unknown_is_not_zero(self):
        for status, label in [("learning", "..."), ("stale", "--"),
                              ("unavailable", "--"), ("exhausted", "0m")]:
            self.assertEqual(time_label({"status": status}), label)

    def test_icons(self):
        for label in ["45m", "2.5h", "99+", "...", "--"]:
            icon = make_icon(label, "ready")
            self.assertEqual(icon.size, (64, 64))
            self.assertEqual(icon.mode, "RGBA")
            self.assertIsNotNone(icon.getbbox())
