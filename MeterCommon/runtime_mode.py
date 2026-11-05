"""追加の常駐プロセスなしで2つのMeterの動作間隔を共有する。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path


def runtime_file():
    """Windowsは従来どおりLOCALAPPDATA。LinuxはXDGの保存先を使い、Windowsと混ぜない。"""
    if os.name != 'nt':
        base = Path(os.environ.get('XDG_DATA_HOME') or str(Path.home() / '.local' / 'share'))
    else:
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local')))
    return base / 'MeterCommon' / 'runtime.json'


class RuntimeMode:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else runtime_file()
        self.lightweight = False
        self._stamp = None
        self.reload()

    @property
    def poll_seconds(self):
        return 30 if self.lightweight else 10

    def reload(self):
        try:
            stat = self.path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if stamp == self._stamp:
                return False
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or type(value.get('lightweight')) is not bool:
                return False
        except (OSError, ValueError):
            return False
        self._stamp = stamp
        changed = self.lightweight != value['lightweight']
        self.lightweight = value['lightweight']
        return changed

    def toggle(self):
        self.reload()
        value = not self.lightweight
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'lightweight': value}, stream)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.lightweight = value
        self._stamp = None


class RuntimeControls:
    """Tkスレッドで設定を同期し、ログ読み取りスレッドを起こす。"""
    def init_runtime(self, mode=None):
        self.runtime_mode = mode if mode is not None else RuntimeMode()
        self.wake = threading.Event()
        self._next_mode_check = 0
        self._next_render = 0

    def mode_caption(self):
        return '軽量' if self.runtime_mode.lightweight else '通常'

    def toggle_runtime_mode(self, *_):
        try:
            self.runtime_mode.toggle()
        except OSError:
            self.status.configure(text='共通の動作モードを保存できませんでした。')
            return
        self.apply_runtime_mode()

    def apply_runtime_mode(self):
        self.mode_button.configure(text=self.mode_caption())
        if self.tray_available:
            self.tray.update_menu()
        self.wake.set()

    def sync_runtime_mode(self):
        now = time.monotonic()
        if now >= self._next_mode_check:
            self._next_mode_check = now + 1
            if self.runtime_mode.reload():
                self.apply_runtime_mode()

    def render_due(self, changed):
        now = time.monotonic()
        if changed or now >= self._next_render:
            self._next_render = now + 30
            return True
        return False
