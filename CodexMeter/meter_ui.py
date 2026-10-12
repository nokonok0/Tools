from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox

from core import LogReader, Observations, build_view, data_directory, forecast

TITLE = "Codex Meter"
BG, INK, MUTED, ACCENT = "#f5f7f8", "#20272d", "#626c74", "#197a68"


def duration(hours):
    minutes = max(1, round(hours * 60))
    if minutes < 60:
        return f"約{minutes}分"
    whole, rest = divmod(minutes, 60)
    return f"約{whole}時間" + (f"{rest}分" if rest else "")


def forecast_text(result):
    status = result["status"]
    if status == "ready":
        return duration(result["hours"])
    return {"learning": "計測中", "stale": "更新待ち", "unavailable": "—",
            "exhausted": "上限到達"}[status]


class Meter:
    def __init__(self, root, tray_enabled=True, show_event=None):
        self.root = root
        self.stop = threading.Event()
        self.requests = queue.Queue()
        self.messages = queue.Queue()
        self.reader = LogReader()
        self.store = Observations()
        self.view = build_view(self.store.events)
        self.busy = False
        self.tray = None
        self.tray_enabled = tray_enabled
        self.tray_available = False
        self.show_event = show_event
        self.alert_path = data_directory() / "notifications.json"
        try:
            saved = json.loads(self.alert_path.read_text(encoding="utf-8"))
            self.notified = set(x for x in saved if isinstance(x, str)) if isinstance(saved, list) else set()
        except (OSError, ValueError):
            self.notified = set()
        self.last_signature = None
        root.title(TITLE)
        if tray_enabled:
            root.withdraw()
        if sys.platform == "win32":
            root.wm_attributes("-toolwindow", True)
        root.configure(bg=BG)
        root.minsize(620, 340)
        width = min(640, root.winfo_screenwidth() - 80)
        height = min(400, root.winfo_screenheight() - 100)
        root.geometry(f"{width}x{height}+60+60")
        root.protocol("WM_DELETE_WINDOW", self.hide)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK, font=("Yu Gothic UI", 10))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Segoe UI", 13, "bold"))
        style.configure("Number.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("TButton", font=("Yu Gothic UI", 10), padding=(10, 5))
        style.configure("Treeview", font=("Yu Gothic UI", 9), rowheight=28,
                        background="#ffffff", fieldbackground="#ffffff", borderwidth=0)
        style.configure("Treeview.Heading", font=("Yu Gothic UI", 9, "bold"), padding=(4, 6))
        style.map("Treeview", background=[("selected", "#d5ebe4")],
                  foreground=[("selected", INK)])
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=TITLE, style="Title.TLabel").pack(side="left")
        self.refresh_button = ttk.Button(header, text="↻", width=3, command=self.refresh)
        self.refresh_button.pack(side="right")
        self.bucket = tk.StringVar()
        self.buckets = ttk.Combobox(header, textvariable=self.bucket, state="readonly", width=16)
        self.buckets.pack(side="right", padx=10)
        self.buckets.bind("<<ComboboxSelected>>", lambda _: self.render())
        self.quota_frames = {}
        band = ttk.Frame(outer, padding=(0, 12, 0, 12))
        band.grid(row=1, column=0, sticky="ew")
        for column, slot in enumerate(("primary", "secondary")):
            frame = ttk.Frame(band, padding=(0 if column == 0 else 22, 0, 22 if column == 0 else 0, 0))
            frame.grid(row=0, column=column, sticky="nsew")
            band.columnconfigure(column, weight=1)
            label = ttk.Label(frame, text="短期枠" if slot == "primary" else "週間枠", style="Muted.TLabel")
            label.pack(anchor="w")
            number = ttk.Label(frame, text="—", style="Number.TLabel")
            number.pack(anchor="w")
            bar = tk.Canvas(frame, height=6, highlightthickness=0, background="#dfe5e8")
            bar.pack(fill="x", pady=(6, 9))
            reset = ttk.Label(frame, text="取得中", style="Muted.TLabel")
            reset.pack(anchor="w")
            self.quota_frames[slot] = (label, number, bar, reset)
            bar.bind("<Configure>", lambda _, s=slot: self.paint_bar(s))
        table_heading = ttk.Frame(outer)
        table_heading.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(table_heading, text="モデル別の残り時間", font=("Yu Gothic UI", 11, "bold")).pack(side="left")
        ttk.Label(table_heading, text="連続使用時の目安", style="Muted.TLabel").pack(side="right")
        table_frame = ttk.Frame(outer)
        table_frame.grid(row=3, column=0, sticky="nsew")
        cols = ("model", "effort", "primary", "secondary", "measured")
        self.table = ttk.Treeview(table_frame, columns=cols, displaycolumns=("model", "primary", "secondary", "measured"),
                                  show="headings", selectmode="browse", height=5)
        titles = ("モデル / 推論", "推論", "上限までの推定時間", "週間枠の推定時間", "実測時間")
        for col, name, width in zip(cols, titles, (190, 65, 140, 140, 75)):
            self.table.heading(col, text=name)
            self.table.column(col, width=width, minwidth=60, stretch=col in ("model", "primary", "secondary"))
        self.table.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table_frame, command=self.table.yview)
        scroll.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.tag_configure("current", foreground=ACCENT)
        self.table.bind("<<TreeviewSelect>>", self.selection)
        self.detail = ttk.Label(outer, text="", style="Muted.TLabel", wraplength=740)
        self.detail.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        self.status = ttk.Label(outer, text="ログを確認中…", style="Muted.TLabel", wraplength=740)
        self.status.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        self.root.bind("<Configure>", self.wrap_labels)
        self.root.bind("<FocusOut>", lambda _: root.after(150, self.dismiss_if_unfocused))
        self.root.bind("<Escape>", lambda _: self.hide())
        if tray_enabled:
            self.setup_tray()
            if self.tray is None:
                root.deiconify()
        self.worker = threading.Thread(target=self.run, name="log-reader", daemon=True)
        self.worker.start()
        root.after(200, self.tick)

    def wrap_labels(self, event):
        if event.widget == self.root:
            self.detail.configure(wraplength=max(100, event.width - 44))
            self.status.configure(wraplength=max(100, event.width - 44))

    def run(self):
        try:
            while not self.stop.is_set():
                forced = False
                while not self.requests.empty():
                    try:
                        self.requests.get_nowait()
                        forced = True
                    except queue.Empty:
                        break
                try:
                    added = self.store.add(self.reader.poll(force=forced))
                    if added or not self.view["latest"]:
                        view = build_view(self.store.events, incomplete=self.reader.truncated)
                        self.messages.put(("view", view))
                    self.store.save()
                    self.messages.put(("done", None))
                except FileNotFoundError:
                    self.messages.put(("error", "Codexのログフォルダが見つかりません。"))
                except Exception as exc:
                    self.messages.put(("error", f"読み取り失敗 ({type(exc).__name__})。次回更新で再試行します。"))
                self.stop.wait(10)
        finally:
            try:
                self.store.save(force=True)
            except OSError:
                pass

    def setup_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            icon = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(icon)
            draw.ellipse((3, 3, 61, 61), fill=ACCENT)
            draw.arc((12, 12, 52, 52), 130, 410, fill="white", width=6)
            draw.line((32, 32, 43, 22), fill="white", width=5)
            self.tray = pystray.Icon("CodexMeter", icon, TITLE, pystray.Menu(
                pystray.MenuItem("表示 / 非表示", self.tray_toggle, default=True),
                pystray.MenuItem("更新", lambda *_: self.requests.put(True)),
                pystray.MenuItem("終了", lambda *_: self.messages.put(("quit", None)))))
            def serve():
                try:
                    self.tray.run(setup=self.prepare_tray)
                except Exception:
                    self.messages.put(("tray_failed", None))
            threading.Thread(target=serve, name="tray", daemon=True).start()
        except ImportError:
            self.tray = None

    def prepare_tray(self, icon):
        icon.visible = True
        self.messages.put(("tray_ready", None))

    def tray_toggle(self, *_):
        if sys.platform == "win32" and getattr(self.tray, "_hwnd", None):
            ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(self.tray._hwnd))
        self.messages.put(("toggle", None))

    def check_notifications(self, point, now):
        if not self.tray_available or not point or now - point["ts"] > 300:
            return
        messages = []
        for slot, name in (("primary", "短期枠"), ("secondary", "週間枠")):
            window = point.get(slot)
            if not window or now >= window["reset"]:
                continue
            remaining = 100 - window["used"]
            level = 10 if remaining <= 10 else (20 if remaining <= 20 else None)
            if level is None:
                continue
            key = f"{point['bucket']}:{slot}:{window['reset']}:{level}"
            if key in self.notified:
                continue
            self.notified.add(key)
            # A first observation below 10% must not later fire the 20% alert.
            if level == 10:
                self.notified.add(f"{point['bucket']}:{slot}:{window['reset']}:20")
            messages.append(f"{name}の残り {remaining:g}%")
        if messages:
            self.tray.notify(" / ".join(messages), TITLE)
            try:
                self.alert_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.alert_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(sorted(self.notified)[-128:]), encoding="utf-8")
                temporary.replace(self.alert_path)
            except OSError:
                pass

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.refresh_button.configure(state="disabled")
        self.requests.put(True)
        self.status.configure(text="更新中…")

    def paint_bar(self, slot):
        _, _, bar, _ = self.quota_frames[slot]
        point = self.view["latest"].get(self.bucket.get()) or {}
        window = point.get(slot)
        bar.delete("all")
        if not window or time.time() >= window["reset"]:
            return
        remaining = 100 - window["used"]
        color = ACCENT if remaining > 30 else ("#b8791b" if remaining > 10 else "#b43e47")
        bar.create_rectangle(0, 0, bar.winfo_width() * remaining / 100, 6, fill=color, outline="")

    def render(self):
        now = time.time()
        buckets = sorted(self.view["latest"])
        self.buckets.configure(values=buckets)
        if self.bucket.get() not in buckets:
            self.bucket.set("codex" if "codex" in buckets else (buckets[0] if buckets else ""))
        bucket = self.bucket.get()
        point = self.view["latest"].get(bucket) or {}
        for slot, (label, number, _, reset) in self.quota_frames.items():
            window = point.get(slot)
            if not window:
                number.configure(text="—")
                reset.configure(text="未取得")
                continue
            minutes = window["minutes"]
            period = f"{minutes // 1440}日" if minutes >= 1440 else f"{minutes / 60:g}時間"
            label.configure(text=f"{period}枠 · 残り")
            expired = now >= window["reset"]
            number.configure(text="更新待ち" if expired else f"{100 - window['used']:g}%")
            when = datetime.fromtimestamp(window["reset"]).strftime("%m/%d %H:%M")
            reset.configure(text=f"リセット {when}" + (" · 要更新" if expired else ""))
            self.paint_bar(slot)
        selected = self.table.selection()
        self.table.delete(*self.table.get_children())
        self.rows = {}
        for index, model in enumerate(self.view["models"]):
            results = [forecast(self.view, model, bucket, slot, now) for slot in ("primary", "secondary")]
            measured = max((self.view["aggregates"].get((model, bucket, s), {}).get("seconds", 0)
                            for s in ("primary", "secondary")), default=0)
            iid = f"model-{index}"
            self.rows[iid] = (model, results)
            self.table.insert("", "end", iid=iid, values=(f"{model[0]} / {model[1]}", model[1],
                              *(forecast_text(r) for r in results), f"{measured / 60:.0f}分"),
                              tags=("current",) if index == 0 else ())
        if selected and selected[0] in self.rows:
            self.table.selection_set(selected[0])
        stamp = self.view["observed_at"]
        if stamp:
            age = max(0, round((now - stamp) / 60))
            when = datetime.fromtimestamp(stamp).strftime("%m/%d %H:%M:%S")
            text = f"残量取得 {when} · ログ監視中"
            if age >= 5:
                text += f" · {age}分前の値"
            if self.view["incomplete"]:
                text += " · 同時実行の全件確認ができないため予測を停止"
            self.status.configure(text=text)
        else:
            self.status.configure(text="使用量の記録待ち · Codexで作業すると残量が表示されます")
        if self.tray:
            short = point.get("primary")
            weekly = point.get("secondary")
            parts = [TITLE]
            for label, w in (("短期", short), ("週間", weekly)):
                if w:
                    if now < w["reset"]:
                        wait = max(0, int((w["reset"] - now) / 60))
                        hours, mins = divmod(wait, 60)
                        parts.append(f"{label}: 残り{100 - w['used']:g}% · 更新まで{hours}h{mins:02d}m")
                    else:
                        parts.append(f"{label}: 更新待ち")
            if self.view["models"]:
                model = self.view["models"][0]
                result = forecast(self.view, model, bucket, "primary", now)
                parts.append(f"{model[0]} / {model[1]} · {forecast_text(result)}")
            if stamp and now - stamp > 300:
                parts.append("過去の観測値")
            self.tray.title = "\n".join(parts).encode("utf-16-le")[:254].decode("utf-16-le", errors="ignore")
            self.check_notifications(point, now)
        self.selection()

    def selection(self, _=None):
        selected = self.table.selection()
        if not selected or selected[0] not in self.rows:
            self.detail.configure(text="")
            return
        model, results = self.rows[selected[0]]
        texts = []
        for name, result in zip(("短期", "週間"), results):
            if result["status"] == "ready":
                text = f"{name}: {result['rate']:.1f}%/時間 · {duration(result['low'])}〜{duration(result['high'])}"
                if result["reset_first"]:
                    text += " · リセットが先"
                texts.append(text)
        self.detail.configure(text=" / ".join(texts) if texts else f"{model[0]} · 推定に必要な単独使用の記録を収集中")

    def tick(self):
        if self.stop.is_set():
            return
        changed = False
        if self.show_event and ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(self.show_event), 0) == 0:
            self.show()
        while not self.messages.empty():
            kind, value = self.messages.get_nowait()
            if kind == "view":
                self.view = value
                changed = True
            elif kind == "done":
                self.busy = False
                self.refresh_button.configure(state="normal")
            elif kind == "show":
                self.show()
            elif kind == "toggle":
                self.hide() if self.root.state() != "withdrawn" else self.show()
            elif kind == "tray_ready":
                self.tray_available = True
                changed = True
            elif kind == "tray_failed":
                self.tray = None
                self.tray_available = False
                self.show()
                self.status.configure(text="トレイを作成できませんでした。ウィンドウ表示で動作中です。")
            elif kind == "quit":
                self.quit()
                return
            elif kind == "error":
                self.status.configure(text=value)
                self.busy = False
                self.refresh_button.configure(state="normal")
        if changed or int(time.time()) % 30 == 0:
            self.render()
        self.dismiss_if_unfocused()
        self.root.after(500, self.tick)

    def hide(self):
        if self.tray_available:
            self.root.withdraw()
        else:
            self.quit()

    def show(self):
        self.root.deiconify()
        self.position_panel()
        self.root.lift()
        if sys.platform == "win32":
            user = ctypes.windll.user32
            user.GetAncestor.restype = ctypes.c_void_p
            window = user.GetAncestor(ctypes.c_void_p(self.root.winfo_id()), 2)
            user.SetForegroundWindow(ctypes.c_void_p(window))
        self.root.focus_force()

    def position_panel(self):
        if sys.platform != "win32":
            return
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
        user = ctypes.windll.user32
        user.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user.MonitorFromPoint.restype = ctypes.c_void_p
        user.GetAncestor.restype = ctypes.c_void_p
        cursor = wintypes.POINT()
        user.GetCursorPos(ctypes.byref(cursor))
        monitor = user.MonitorFromPoint(cursor, 2)
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        if not user.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info)):
            return
        self.root.update_idletasks()
        window = user.GetAncestor(ctypes.c_void_p(self.root.winfo_id()), 2)
        rect = wintypes.RECT()
        user.GetWindowRect(ctypes.c_void_p(window), ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        work = info.work
        # All values here are native screen coordinates, including DPI scaling.
        x = max(work.left, work.right - width - 12)
        y = max(work.top, work.bottom - height - 12)
        user.SetWindowPos(ctypes.c_void_p(window), None, x, y, 0, 0, 0x0001 | 0x0004)

    def dismiss_if_unfocused(self):
        if not self.tray_available or self.root.state() == "withdrawn":
            return
        if sys.platform == "win32":
            user = ctypes.windll.user32
            user.GetForegroundWindow.restype = ctypes.c_void_p
            foreground = user.GetForegroundWindow()
            pid = ctypes.c_ulong()
            user.GetWindowThreadProcessId(ctypes.c_void_p(foreground), ctypes.byref(pid))
            if foreground and pid.value != os.getpid():
                self.root.withdraw()
        elif self.root.focus_displayof() is None:
            self.root.withdraw()

    def quit(self):
        self.stop.set()
        if self.tray:
            self.tray.stop()
        self.worker.join(timeout=3)
        self.root.destroy()


def main():
    mutex = None
    show_event = None
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        name = "Local\\CodexMeter-User-" + str(data_directory()).replace("\\", "_")
        mutex = kernel.CreateMutexW(None, False, name)
        if ctypes.get_last_error() == 183:
            user = ctypes.windll.user32
            user.FindWindowW.restype = ctypes.c_void_p
            window = user.FindWindowW(None, TITLE)
            pid = ctypes.c_ulong()
            if window:
                user.GetWindowThreadProcessId(ctypes.c_void_p(window), ctypes.byref(pid))
                user.AllowSetForegroundWindow(pid.value)
            kernel.OpenEventW.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_wchar_p]
            kernel.OpenEventW.restype = ctypes.c_void_p
            request = kernel.OpenEventW(2, False, name + "-Show")
            if request:
                kernel.SetEvent(ctypes.c_void_p(request))
                kernel.CloseHandle(ctypes.c_void_p(request))
            kernel.CloseHandle(ctypes.c_void_p(mutex))
            return
        kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateEventW.restype = ctypes.c_void_p
        show_event = kernel.CreateEventW(None, False, False, name + "-Show")
    root = tk.Tk()
    try:
        Meter(root, show_event=show_event)
        root.mainloop()
    except Exception as exc:
        messagebox.showerror(TITLE, f"起動に失敗しました ({type(exc).__name__})。", parent=root)
        raise
    finally:
        if mutex:
            kernel.CloseHandle(ctypes.c_void_p(mutex))
        if show_event:
            kernel.CloseHandle(ctypes.c_void_p(show_event))


if __name__ == "__main__":
    main()
