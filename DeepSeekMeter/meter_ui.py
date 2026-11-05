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
from pathlib import Path
from tkinter import ttk, messagebox

# 直接起動と各アプリのvenvから共通モジュールを参照する。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from MeterCommon.runtime_mode import RuntimeControls

from balance import fetch_balance, money
from core import (LogReader, Observations, build_view, data_directory,
                  default_home, forecast)
from tray_display import icon_text, make_icon, time_label

TITLE = "DeepSeek Meter"
BG, INK, MUTED, ACCENT = "#f5f7f8", "#20272d", "#626c74", "#197a68"
THRESHOLDS = {"USD": (1.0, 0.5), "CNY": (7.0, 3.5)}


def duration(hours):
    minutes = max(1, round(hours * 60))
    if minutes < 60:
        return f"約{minutes}分"
    whole, rest = divmod(minutes, 60)
    return f"約{whole}時間" + (f"{rest}分" if rest else "")


def forecast_text(result):
    status = result.get("status")
    if status in ("ready", "stale") and "hours" in result:
        text = "残高なし" if result["hours"] <= 0 else duration(result["hours"])
        return text + (" ※" if status == "stale" else "")
    return {"learning": "計測中", "stale": "更新待ち", "unavailable": "—",
            "exhausted": "残高なし", "auth": "要ログイン",
            "failed": "取得失敗"}.get(status, "—")


def model_text(model):
    return model.removeprefix("deepseek-") or model


def effort_text(effort):
    return {"none": "無", "minimal": "最小", "low": "低", "medium": "中",
            "high": "高", "xhigh": "最高", "max": "最大", "ultra": "超",
            "off": "無", "default": "既定"}.get(effort, effort)


def rate_text(result):
    if result.get("status") in ("ready", "stale") and "rate" in result:
        return money(result.get("currency"), result["rate"]) + "/時"
    return "—"


def clock(ts):
    return datetime.fromtimestamp(ts).strftime("%m/%d %H:%M") if ts else "—"


def token_text(value):
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 10_000:
        return f"{value / 1_000:.0f}k"
    return f"{int(value):,}"


class Meter(RuntimeControls):
    def __init__(self, root, tray_enabled=True, show_event=None):
        self.root = root
        self.stop = threading.Event()
        self.init_runtime()
        self.requests = queue.Queue()
        self.balance_requests = queue.Queue()
        self.messages = queue.Queue()
        self.balance_events = queue.Queue()
        self.reader = LogReader()
        self.store = Observations()
        self.view = build_view(self.store.events)
        self.result = {"status": "unavailable"}
        self.balance = {"status": "loading"}
        self.busy = False
        self.tray = None
        self.tray_enabled = tray_enabled
        self.tray_available = False
        self.show_event = show_event
        self.alert_path = data_directory() / "notifications.json"
        try:
            saved = json.loads(self.alert_path.read_text(encoding="utf-8"))
            self.alerts = saved if isinstance(saved, dict) else {}
        except (OSError, ValueError):
            self.alerts = {}
        self.alerts.setdefault("level", 0)
        self.last_icon_signature = None
        self.last_title = None
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
        style.configure("TLabel", background=BG, foreground=INK, font=("Yu Gothic UI", 9))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Number.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", font=("Yu Gothic UI", 9), padding=(6, 2))
        style.configure("Treeview", font=("Yu Gothic UI", 8), rowheight=21,
                        background="#ffffff", fieldbackground="#ffffff", borderwidth=0)
        style.configure("Treeview.Heading", font=("Yu Gothic UI", 8, "bold"), padding=(3, 2))
        style.map("Treeview", background=[("selected", "#d5ebe4")],
                  foreground=[("selected", INK)])
        outer = ttk.Frame(root, padding=8)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=TITLE, style="Title.TLabel").pack(side="left")
        self.refresh_button = ttk.Button(header, text="↻", width=3, command=self.refresh)
        self.refresh_button.pack(side="right")
        self.mode_button = ttk.Button(header, text=self.mode_caption(), width=4,
                                      command=self.toggle_runtime_mode)
        self.mode_button.pack(side="right", padx=3)
        self.currency_label = ttk.Label(header, text="", style="Muted.TLabel")
        self.currency_label.pack(side="right", padx=10)

        band = ttk.Frame(outer, padding=(0, 4, 0, 2))
        band.grid(row=1, column=0, sticky="ew")
        self.cards = {}
        for column, (key, caption) in enumerate((("balance", "残高"), ("left", "枯渇まで"),
                                                 ("rate", "消費ペース"))):
            band.columnconfigure(column, weight=1, uniform="card")
            ttk.Label(band, text=caption, style="Muted.TLabel").grid(
                row=0, column=column, sticky="w", padx=(0, 10))
            number = ttk.Label(band, text="—", style="Number.TLabel")
            number.grid(row=1, column=column, sticky="w", padx=(0, 10))
            note = ttk.Label(band, text="取得中", style="Muted.TLabel")
            note.grid(row=2, column=column, sticky="w", padx=(0, 10))
            self.cards[key] = (number, note)

        sub = ttk.Frame(outer)
        sub.grid(row=2, column=0, sticky="ew", pady=(2, 4))
        self.spend_labels = {}
        for column, (key, caption) in enumerate((("today", "本日"), ("day", "24時間"),
                                                 ("total", "累計"), ("updated", "取得"))):
            sub.columnconfigure(column, weight=1, uniform="spend")
            label = ttk.Label(sub, text=f"{caption} —", style="Muted.TLabel")
            label.grid(row=0, column=column, sticky="w", padx=(0, 8))
            self.spend_labels[key] = label

        table_frame = ttk.Frame(outer)
        table_frame.grid(row=3, column=0, sticky="nsew")
        cols = ("model", "effort", "calls", "input", "cached", "output", "total", "last")
        titles = ("モデル", "推論", "回数", "入力", "キャッシュ", "出力", "合計", "最終使用")
        widths = (95, 40, 40, 60, 88, 55, 70, 105)
        self.table = ttk.Treeview(table_frame, columns=cols, show="headings",
                                  selectmode="browse", height=4)
        for col, name, width in zip(cols, titles, widths):
            self.table.heading(col, text=name)
            self.table.column(col, width=width, minwidth=40, stretch=col == "model",
                              anchor="w" if col in ("model", "effort", "last") else "e")
        scroll = ttk.Scrollbar(table_frame, command=self.table.yview)
        scroll.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.selection)
        self.detail = ttk.Label(outer, text="", style="Muted.TLabel", wraplength=740)
        self.detail.grid(row=4, column=0, sticky="ew", pady=(3, 0))
        self.status = ttk.Label(outer, text="DSHのセッションログを確認中…", style="Muted.TLabel",
                                wraplength=740)
        self.status.grid(row=5, column=0, sticky="ew", pady=(2, 0))
        self.root.bind("<Configure>", self.wrap_labels)
        self.root.bind("<FocusOut>", lambda _: root.after(150, self.dismiss_if_unfocused))
        self.root.bind("<Escape>", lambda _: self.hide())
        if tray_enabled:
            self.setup_tray()
            if self.tray is None:
                root.deiconify()
        self.worker = threading.Thread(target=self.run, name="log-reader", daemon=True)
        self.worker.start()
        self.balance_worker = threading.Thread(target=self.run_balance, name="balance-reader",
                                               daemon=True)
        self.balance_worker.start()
        root.after(200, self.tick)

    def run_balance(self):
        deadline = 0
        while not self.stop.is_set():
            forced = False
            while not self.balance_requests.empty():
                try:
                    self.balance_requests.get_nowait()
                    forced = True
                except queue.Empty:
                    break
            if time.monotonic() >= deadline or forced:
                result = fetch_balance()
                if result["status"] == "ready":
                    self.balance_events.put({"kind": "balance", "session": "deepseek-balance",
                                             "ts": result["ts"], "balance": result["balance"],
                                             "currency": result["currency"],
                                             "total_cost": result.get("total_cost", 0.0)})
                    self.wake.set()
                self.messages.put(("balance", result))
                deadline = time.monotonic() + 300
            self.stop.wait(1)

    def wrap_labels(self, event):
        if event.widget == self.root:
            self.detail.configure(wraplength=max(100, event.width - 44))
            self.status.configure(wraplength=max(100, event.width - 44))

    def run(self):
        while not self.stop.is_set():
            self.wake.clear()
            if self.stop.is_set():
                break
            forced = False
            while not self.requests.empty():
                try:
                    self.requests.get_nowait()
                    forced = True
                except queue.Empty:
                    break
            try:
                try:
                    self.store.add(self.reader.poll(force=forced))
                except FileNotFoundError:
                    self.messages.put(("error", "DeepSeek Harnessのセッションログが見つかりません。"))
                    self.reader.truncated = True
                while not self.balance_events.empty():
                    try:
                        self.store.add([self.balance_events.get_nowait()])
                    except queue.Empty:
                        break
                view = build_view(self.store.events, incomplete=self.reader.truncated)
                self.messages.put(("view", view))
                self.store.save()
                self.messages.put(("done", None))
            except Exception as exc:
                self.messages.put(("error", f"読み取り失敗 ({type(exc).__name__})。次回更新で再試行します。"))
            self.wake.wait(self.runtime_mode.poll_seconds)
        try:
            self.store.save(force=True)
        except OSError:
            pass

    def setup_tray(self):
        try:
            import pystray
            icon = make_icon("...", "learning")
            self.tray = pystray.Icon("DeepSeekMeter", icon, TITLE, pystray.Menu(
                pystray.MenuItem("表示 / 非表示", self.tray_toggle, default=True),
                pystray.MenuItem("更新", lambda *_: self.request_refresh()),
                pystray.MenuItem("軽量モード（Meter共通）",
                                 lambda *_: self.messages.put(("mode_toggle", None)),
                                 checked=lambda _: self.runtime_mode.lightweight),
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

    def request_refresh(self):
        self.requests.put(True)
        self.balance_requests.put(True)
        self.wake.set()

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.refresh_button.configure(state="disabled")
        self.request_refresh()
        self.status.configure(text="更新中…")

    def check_notifications(self, point, now):
        if not self.tray_available or not point or now - point["ts"] > 300:
            return
        currency = point.get("currency") or "USD"
        balance = float(point.get("balance") or 0)
        first, second = THRESHOLDS.get(currency, THRESHOLDS["USD"])
        saved = self.alerts if self.alerts.get("currency") == currency else {"level": 0}
        level = 2 if balance <= second else (1 if balance <= first else 0)
        if balance > first * 1.5:
            level = 0
        previous = int(saved.get("level", 0))
        state = {"currency": currency,
                 "level": max(level, previous if level else 0),
                 "seen": True}
        if state != self.alerts:
            self.alerts = state
            if level > previous and saved.get("seen"):
                self.tray.notify(f"DeepSeekの残高が {money(currency, balance)} になりました", TITLE)
            try:
                self.alert_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.alert_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(self.alerts), encoding="utf-8")
                temporary.replace(self.alert_path)
            except OSError:
                pass

    def tick(self):
        if self.stop.is_set():
            return
        self.sync_runtime_mode()
        changed = False
        if self.show_event and ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(self.show_event), 0) == 0:
            self.show()
        while not self.messages.empty():
            kind, value = self.messages.get_nowait()
            if kind == "view":
                self.view = value
                self.result = forecast(value)
                changed = True
            elif kind == "balance":
                self.balance = value
                if value["status"] in ("auth", "failed", "unavailable"):
                    self.status.configure(text={
                        "auth": "DeepSeekアカウントの認証が切れています。DSHで再ログインしてください。",
                        "unavailable": "DSHのDeepSeekアカウント認証情報が見つかりません。",
                        "failed": f"残高を取得できませんでした ({value.get('reason', '')})。",
                    }[value["status"]])
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
            elif kind == "mode_toggle":
                self.toggle_runtime_mode()
            elif kind == "quit":
                self.quit()
                return
            elif kind == "error":
                self.status.configure(text=value)
                self.busy = False
                self.refresh_button.configure(state="normal")
        if self.render_due(changed):
            self.render()
            self.update_tray()
        self.dismiss_if_unfocused()
        self.root.after(500, self.tick)

    def render(self):
        result = self.result
        point = self.view.get("latest_balance")
        currency = (point or {}).get("currency") or result.get("currency") or "USD"
        self.currency_label.configure(text="USD" if currency == "USD" else "CNY")
        number, note = self.cards["balance"]
        if point:
            number.configure(text=money(currency, point.get("balance")))
            note.configure(text="取得 " + clock(point["ts"]))
        else:
            number.configure(text="—")
            note.configure(text="未取得")
        number, note = self.cards["left"]
        number.configure(text=time_label(result.get("hours")))
        if result.get("status") == "stale" and "hours" in result:
            note.configure(text="前回値 " + clock(result.get("observed_at")))
        elif result.get("status") == "learning":
            note.configure(text="消費ペースを計測中")
        elif result.get("status") == "ready":
            note.configure(text="残高 ÷ 実測ペース")
        else:
            note.configure(text=forecast_text(result))
        number, note = self.cards["rate"]
        number.configure(text=rate_text(result))
        note.configure(text=("実測 " + duration(result["active"] / 3600)) if result.get("active") else "—")

        self.spend_labels["today"].configure(
            text="本日 " + (money(currency, self.view.get("spend_today")) if point else "—"))
        self.spend_labels["day"].configure(
            text="24時間 " + (money(currency, self.view.get("spend_24h")) if point else "—"))
        total = (point or {}).get("total_cost")
        self.spend_labels["total"].configure(
            text="累計 " + (money(currency, total) if total is not None else "—"))
        self.spend_labels["updated"].configure(
            text="取得 " + (clock(point["ts"]) if point else "—"))

        selected = self.table.selection()
        keep = selected[0] if selected else None
        rows = sorted(self.view.get("usage", {}).items(),
                      key=lambda item: item[1].get("last", 0), reverse=True)
        self.table.delete(*self.table.get_children())
        for (model, effort), usage in rows:
            key = f"{model}|{effort}"
            self.table.insert("", "end", iid=key, values=(
                model_text(model), effort_text(effort), f"{usage.get('calls', 0):,}",
                token_text(usage.get("input_tokens", 0)),
                token_text(usage.get("cached_input_tokens", 0)),
                token_text(usage.get("output_tokens", 0)),
                token_text(usage.get("total_tokens", 0)),
                datetime.fromtimestamp(usage.get("last", 0)).strftime("%m/%d %H:%M")))
        if keep and self.table.exists(keep):
            self.table.selection_set(keep)
        if not rows:
            self.detail.configure(text="DSHの使用記録を収集中です。")
        elif not selected:
            self.detail.configure(text="行を選ぶとトークンの内訳を確認できます。")

        if self.balance.get("status") == "ready":
            self.status.configure(
                text=f"DSHログ {len(self.view.get('usage_events', [])):,}件 · 残高 "
                     f"{'更新済み' if point else '待機中'} · 保存 {len(self.store.events):,}件"
                     + (" · 読取範囲を超えたため一部未集計" if self.view.get("incomplete") else ""))

    def selection(self, _=None):
        selected = self.table.selection()
        if not selected:
            return
        model, _, effort = selected[0].rpartition("|")
        usage = self.view.get("usage", {}).get((model, effort))
        if not usage:
            return
        self.detail.configure(text=(
            f"{model} / {effort_text(effort)} · 呼び出し {usage.get('calls', 0):,}回 · "
            f"入力 {usage.get('input_tokens', 0):,} · "
            f"キャッシュ {usage.get('cached_input_tokens', 0):,} · "
            f"キャッシュ書込 {usage.get('cache_write_input_tokens', 0):,} · "
            f"出力 {usage.get('output_tokens', 0):,} · "
            f"合計 {usage.get('total_tokens', 0):,} · "
            f"初回 {clock(usage.get('first'))} · 最終 {clock(usage.get('last'))}"))

    def update_tray(self):
        if not self.tray_available:
            return
        point = self.view.get("latest_balance") or {}
        status = self.result.get("status", "unavailable")
        label = icon_text(self.result)
        signature = (label, status)
        if signature != self.last_icon_signature:
            self.tray.icon = make_icon(label, status)
            self.last_icon_signature = signature
        currency = point.get("currency") or "USD"
        title = (f"残高 {money(currency, point.get('balance')) if point else '—'}\n"
                 f"枯渇まで {forecast_text(self.result)}\n"
                 f"消費ペース {rate_text(self.result)}\n"
                 f"最終取得 {clock(point.get('ts')) if point else '—'}")
        if title != self.last_title:
            self.tray.title = title
            self.last_title = title
        if point:
            self.check_notifications(point, time.time())

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
        self.wake.set()
        if self.tray:
            self.tray.stop()
        self.worker.join(timeout=3)
        self.balance_worker.join(timeout=3)
        self.root.destroy()


def main():
    mutex = None
    show_event = None
    kernel = None
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        name = "Local\\DeepSeekMeter-User-" + str(data_directory()).replace("\\", "_")
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
        if mutex and kernel:
            kernel.CloseHandle(ctypes.c_void_p(mutex))
        if show_event and kernel:
            kernel.CloseHandle(ctypes.c_void_p(show_event))


if __name__ == "__main__":
    main()
