import unittest

import tray_display


class LabelTests(unittest.TestCase):
    def test_icon_shows_remaining_time(self):
        self.assertEqual(tray_display.icon_text({"status": "ready", "hours": 19.83}), "20h")
        self.assertEqual(tray_display.icon_text({"status": "ready", "hours": 0.5}), "30m")
        self.assertEqual(tray_display.icon_text({"status": "stale", "hours": 2.0}), "2h")
        self.assertEqual(tray_display.icon_text({"status": "learning"}), "...")
        self.assertEqual(tray_display.icon_text({"status": "unavailable"}), "--")
        self.assertEqual(tray_display.icon_text({"status": "exhausted", "hours": 0}), "0m")

    def test_time_label(self):
        self.assertEqual(tray_display.time_label(0.5), "30m")
        self.assertEqual(tray_display.time_label(2.5), "2.5h")
        self.assertEqual(tray_display.time_label(12), "12h")
        self.assertEqual(tray_display.time_label(None), "--")

    def test_icon_size(self):
        image = tray_display.make_icon("$4.9", "ready")
        self.assertEqual(image.size, (64, 64))


if __name__ == "__main__":
    unittest.main()
