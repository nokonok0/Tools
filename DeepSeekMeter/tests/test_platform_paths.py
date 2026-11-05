"""保存先がOSごとに分かれ、Windowsの従来の場所を変えないことを確認する。"""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import core


class DataDirectoryTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Linuxの保存先の確認")
    def test_linux_uses_xdg_data_home(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/xdg-home"}, clear=False):
            self.assertEqual(core.data_directory(), Path("/tmp/xdg-home/DeepSeekMeter"))

    @unittest.skipUnless(os.name == "nt", "Windowsの保存先の確認")
    def test_windows_keeps_local_app_data(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": "C:/Users/example/AppData/Local"}, clear=False):
            self.assertEqual(core.data_directory(),
                             Path("C:/Users/example/AppData/Local/DeepSeekMeter"))


if __name__ == "__main__":
    unittest.main()
