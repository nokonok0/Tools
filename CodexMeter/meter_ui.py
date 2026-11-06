from __future__ import annotations

import ctypes
import json
import os
import queue
import re
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
from MeterCommon.appearance import (BG, PANEL, INK, MUTED, ACCENT, LINE, SELECTED,
                                    FONT, apply_theme, enable_dpi_awareness, pixels)

from core import LogReader, Observations, build_view, data_directory, default_home, forecast, full_forecast
from tray_display import make_icon, time_label
from credits import fetch_credits, credits_text
from history_archive import archive_on_startup
from orchestration import orchestration_forecast
from last_forecast import LastForecast
from delegation import load_delegation, DelegationNotices, profile_text

TITLE = "Codex Meter"
BLUE, WARN, DANGER = "#427a99", "#a57529", "#b94b50"
NUM_FONT = ("Segoe UI Semibold", 12)


def credit_short(result):
    if "remaining" in result:
        stale = result.get("status") != "ready"
        return f"OR ${result['remaining']:.2f}" + (" …" if stale else "")
    return {"loading": "OR …", "missing": "OR 未設定", "auth": "OR 認証",
            "error": "OR 失敗"}.get(result.get("status"), "OR —")


def neutral_title(root):
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetAncestor(ctypes.c_void_p(root.winfo_id()), 2)
        enabled = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
    except Exception:
        pass


def duration(hours):
    minutes = max(1, round(hours * 60))
    if minutes < 60:
        return f"約{minutes}分"
    whole, rest = divmod(minutes, 60)
    return f"約{whole}時間" + (f"{rest}分" if rest else "")


def forecast_text(result):
    status = result["status"]
    if result.get("historical") and "hours" in result:
        return ("上限到達" if result["hours"] <= 0 else duration(result["hours"])) + " ※"
    if status == "ready":
        return duration(result["hours"])
    return {"learning": "計測中", "stale": "更新待ち", "unavailable": "—",
            "exhausted": "上限到達"}[status]


def model_text(model):
    name = model.removeprefix("gpt-")
    generation, separator, family = name.rpartition("-")
    symbol = {"sol": "🌞", "terra": "🌍", "luna": "🌙", "astra": "⭐"}.get(family)
    return generation + symbol if model.startswith("gpt-") and separator and symbol else name


def effort_text(effort):
    return {"none": "無", "minimal": "最小", "low": "低", "medium": "中",
            "high": "高", "xhigh": "最高", "max": "最大", "ultra": "超",
            "default": "既定"}.get(effort, effort)


def order_models(models, active=()):
    levels = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "default")
    def key(item):
        name, effort = item
        match = re.match(r"^gpt-(\d+)(?:\.(\d+))?(?:-|$)", name, re.IGNORECASE)
        generation = (-int(match.group(1)), -int(match.group(2) or 0)) if match else (0, 0)
        return (item not in active, *generation, name.casefold(), levels.index(effort) if effort in levels else len(levels), effort)
    return sorted(models, key=key)


def composition_text(signature, *, full=False):
    return " ＋ ".join((f"{model} / {effort} ×{count}" if full else
                        f"{model_text(model)} {effort_text(effort)}×{count}")
                       for (model, effort), count in signature) or "稼働記録なし"


def rate_text(result):
    return (f"{result['rate']:.2f}" + (" ※" if result.get("historical") else "")
            if "rate" in result else forecast_text(result))


def full_text(result):
    return (duration(result["full_hours"]) + (" ※" if result.get("historical") else "")
            if "full_hours" in result else forecast_text(result))


def previous_note(result):
    if result.get("historical"):
        when = datetime.fromtimestamp(result["observed_at"]).strftime("%m/%d %H:%M")
        return f"※前回値 {when}時点"
    return ""


class Meter(RuntimeControls):
    def __init__(self, root, tray_enabled=True, show_event=None):
        self.root = root
        self.stop = threading.Event()
        self.init_runtime()
        self.requests = queue.Queue()
        self.messages = queue.Queue()
        self.reader = LogReader()
        self.store = Observations()
        self.last_forecasts = LastForecast(data_directory() / "last_forecasts.json")
        self.view = build_view(self.store.events, history=self.store.history)
        self.delegation = {"status": "loading", "groups": [], "comparisons": [], "alerts": [],
                           "count": 0, "passes": 0, "rework": 0, "excluded": 0, "pending": 0, "errors": 0}
        self.delegation_notices = DelegationNotices(data_directory() / "delegation_notifications.json")
        self.credits = {"status": "loading"}
        self.credit_requests = queue.Queue()
        self.credit_observations = queue.Queue()
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
        self.last_icon_signature = None
        root.title(TITLE)
        if tray_enabled:
            root.withdraw()
        if sys.platform == "win32":
            root.wm_attributes("-toolwindow", True)
        root.configure(bg=BG)
        root.minsize(pixels(root, 520), pixels(root, 280))
        width = min(pixels(root, 560), root.winfo_screenwidth() - 80)
        height = min(pixels(root, 310), root.winfo_screenheight() - 100)
        root.geometry(f"{width}x{height}+60+60")
        root.protocol("WM_DELETE_WINDOW", self.hide)
        neutral_title(root)
        apply_theme(root)
        outer = ttk.Frame(root, padding=8)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="●", style="Dot.TLabel").pack(side="left")
        ttk.Label(header, text="CODEX METER", style="Title.TLabel").pack(side="left", padx=(3, 0))
        self.error_label = ttk.Label(header, text="", style="Error.TLabel")
        self.error_label.pack(side="left", padx=(8, 0))
        self.refresh_button = ttk.Button(header, text="↻", width=2, command=self.refresh)
        self.refresh_button.pack(side="right")
        self.mode_button = ttk.Button(header, text=self.mode_caption(), width=4,
                                      command=self.toggle_runtime_mode)
        self.mode_button.pack(side="right", padx=3)
        self.bucket = tk.StringVar()
        self.buckets = ttk.Combobox(header, textvariable=self.bucket, state="readonly", width=10)
        self.buckets.pack(side="right", padx=6)
        self.buckets.bind("<<ComboboxSelected>>", lambda _: self.render())
        self.header_credit = ttk.Label(header, text="", style="Credit.TLabel")
        self.header_credit.pack(side="right", padx=6)
        self.quota_frames = {}
        band = ttk.Frame(outer)
        band.grid(row=1, column=0, sticky="ew", pady=(4, 3))
        for column, slot in enumerate(("primary", "secondary")):
            frame = ttk.Frame(band)
            frame.grid(row=0, column=column, sticky="nsew", padx=(0, 10) if column == 0 else 0)
            band.columnconfigure(column, weight=1, uniform="quota")
            label = ttk.Label(frame, text="", style="Muted.TLabel")
            label.grid(row=0, column=0, columnspan=2, sticky="w")
            number = tk.Label(frame, text="—", font=NUM_FONT, bg=BG, fg=INK)
            number.grid(row=1, column=0, sticky="w", pady=(1, 0))
            badge = tk.Label(frame, text="", font=(FONT, 8), bg=BG, fg=ACCENT)
            badge.grid(row=1, column=1, sticky="e")
            frame.columnconfigure(0, weight=1)
            bar = tk.Canvas(frame, height=pixels(root, 4), highlightthickness=0, background=LINE)
            bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(3, 2))
            reset = ttk.Label(frame, text="", style="Muted.TLabel")
            reset.grid(row=3, column=0, columnspan=2, sticky="w")
            self.quota_frames[slot] = (label, number, bar, reset, badge)
            bar.bind("<Configure>", lambda _, s=slot: self.paint_bar(s))
        table_heading = ttk.Frame(outer)
        table_heading.grid(row=2, column=0, sticky="ew", pady=(0, 3))
        self.display_mode = tk.StringVar(value="全体・構成")
        modes = ttk.Combobox(table_heading, textvariable=self.display_mode,
                             values=("全体・構成", "モデル単独", "委任の実績"), state="readonly", width=10)
        modes.pack(side="left")
        modes.bind("<<ComboboxSelected>>", lambda _: self.render())
        self.table_note = ttk.Label(table_heading, text="連続使用時の目安", style="Muted.TLabel")
        self.table_note.pack(side="right")
        table_frame = ttk.Frame(outer)
        table_frame.grid(row=3, column=0, sticky="nsew")
        cols = ("model", "effort", "primary", "secondary", "measured", "tokens", "rate", "full")
        self.usage_columns = ("model", "primary", "secondary", "rate", "full", "measured", "tokens")
        self.delegation_columns = ("task", "candidate", "count", "passed", "rework", "observed", "elapsed", "comparison")
        self.table = ttk.Treeview(table_frame, columns=cols + self.delegation_columns, displaycolumns=self.usage_columns,
                                  show="headings", selectmode="browse", height=4)
        titles = ("構成 / 推論", "推論", "残り", "週間", "実測", "トークン", "消費/時", "100%")
        for col, name, width in zip(cols, titles, (145, 40, 85, 75, 40, 60, 55, 85)):
            self.table.heading(col, text=name)
            self.table.column(col, width=pixels(root, width), minwidth=pixels(root, 40), stretch=col in ("model", "primary", "secondary"))
        for col, title, width in zip(self.delegation_columns,
                                    ("作業", "モデル / 推論", "件数", "合格", "手直し", "親＋子トークン", "所要時間", "変更前後"),
                                    (110, 80, 40, 45, 45, 95, 66, 72)):
            self.table.heading(col, text=title)
            self.table.column(col, width=pixels(root, width), minwidth=pixels(root, 40), stretch=col == "task")
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        horizontal.pack(side="bottom", fill="x")
        self.table.configure(xscrollcommand=horizontal.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table_frame, command=self.table.yview)
        scroll.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.tag_configure("current", foreground=INK, background=SELECTED)
        self.table.bind("<<TreeviewSelect>>", self.selection)
        # 説明用の灰色テキストは表示しない。既存の更新処理との互換のためラベルは保持する。
        self.detail = ttk.Label(outer, text="", style="Muted.TLabel")
        self.status = ttk.Label(outer, text="", style="Muted.TLabel")
        self.credit_label = ttk.Label(outer, text="", style="Muted.TLabel")
        self.root.bind("<Configure>", self.wrap_labels)
        self.root.bind("<FocusOut>", lambda _: root.after(150, self.dismiss_if_unfocused))
        self.root.bind("<Escape>", lambda _: self.hide())
        if tray_enabled:
            self.setup_tray()
            if self.tray is None:
                root.deiconify()
        self.worker = threading.Thread(target=self.run, name="log-reader", daemon=True)
        self.worker.start()
        self.credit_worker = threading.Thread(target=self.run_credits, name="credit-reader", daemon=True)
        self.credit_worker.start()
        root.after(200, self.tick)

    def run_credits(self):
        deadline = 0
        while not self.stop.is_set():
            forced = False
            while not self.credit_requests.empty():
                try:
                    self.credit_requests.get_nowait()
                    forced = True
                except queue.Empty:
                    break
            if time.monotonic() >= deadline or forced:
                result = fetch_credits()
                if result["status"] == "ready":
                    self.credit_observations.put({"kind": "credit", "session": "openrouter-credit",
                                                 **{k: result[k] for k in ("ts", "remaining", "purchased", "spent", "account")}})
                    self.requests.put(True)
                    self.wake.set()
                self.messages.put(("credits", result))
                deadline = time.monotonic() + 300
            self.stop.wait(1)

    def wrap_labels(self, event):
        if event.widget == self.root:
            self.detail.configure(wraplength=max(100, event.width - 44))
            self.status.configure(wraplength=max(100, event.width - 44))

    def run(self):
        archive_checked = False
        try:
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
                    self.messages.put(("delegation", load_delegation(default_home() / "delegation")))
                    added = self.store.add(self.reader.poll(force=forced))
                    if not archive_checked:
                        if not self.reader.truncated:
                            result = archive_on_startup(self.store)
                            added = added or result == "archived"
                            if result in ("failed", "offline"):
                                self.messages.put(("error", "履歴の退避先に接続できないか退避に失敗しました。ローカルで保存を続けます。"))
                            elif result == "untrusted":
                                self.messages.put(("error", "履歴の退避先が未登録か登録先と異なります。退避せずローカルで保存を続けます。"))
                        archive_checked = True
                    while not self.credit_observations.empty():
                        try:
                            added = self.store.add([self.credit_observations.get_nowait()]) or added
                        except queue.Empty:
                            break
                    # Active membership and the 15-minute window age even when
                    # the log receives no new events.
                    view = build_view(self.store.events, incomplete=self.reader.truncated,
                                      history=self.store.history)
                    self.messages.put(("view", view))
                    self.store.save()
                    self.messages.put(("done", None))
                except FileNotFoundError:
                    self.messages.put(("error", "Codexのログフォルダが見つかりません。"))
                except Exception as exc:
                    self.messages.put(("error", f"読み取り失敗 ({type(exc).__name__})。次回更新で再試行します。"))
                self.wake.wait(self.runtime_mode.poll_seconds)
        finally:
            try:
                self.store.save(force=True)
            except OSError:
                pass

    def setup_tray(self):
        try:
            import pystray
            icon = make_icon("...", "learning")
            self.tray = pystray.Icon("CodexMeter", icon, TITLE, pystray.Menu(
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

    def request_refresh(self):
        self.requests.put(True)
        self.credit_requests.put(True)
        self.wake.set()

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.refresh_button.configure(state="disabled")
        self.request_refresh()
        self.status.configure(text="更新中…")

    def paint_bar(self, slot):
        _, _, bar, _, _ = self.quota_frames[slot]
        point = self.view["latest"].get(self.bucket.get()) or {}
        window = point.get(slot)
        bar.delete("all")
        width = max(1, bar.winfo_width())
        if not window or time.time() >= window["reset"]:
            return
        remaining = 100 - window["used"]
        color = ACCENT if remaining > 30 else (WARN if remaining > 10 else DANGER)
        bar.create_rectangle(0, 0, width * remaining / 100, bar.winfo_height(), fill=color, outline="")

    def render(self):
        now = time.time()
        self.credit_label.configure(text=credits_text(self.credits, now))
        self.header_credit.configure(text=credit_short(self.credits))
        buckets = sorted(self.view["latest"])
        self.buckets.configure(values=buckets)
        if self.bucket.get() not in buckets:
            self.bucket.set("codex" if "codex" in buckets else (buckets[0] if buckets else ""))
        bucket = self.bucket.get()
        point = self.view["latest"].get(bucket) or {}
        runs = (self.view.get("weekly_runs") or {}).get(bucket) or {}
        for slot, (label, number, _, reset, badge) in self.quota_frames.items():
            window = point.get(slot)
            if not window:
                number.configure(text="—", fg=INK)
                reset.configure(text="未取得")
                badge.configure(text="")
                continue
            minutes = window["minutes"]
            period = f"{minutes // 1440}日" if minutes >= 1440 else f"{minutes / 60:g}時間"
            label.configure(text=f"{period}枠 · 残り")
            expired = now >= window["reset"]
            old = expired or now - point["ts"] > 300
            remaining = 100 - window["used"]
            number.configure(text=f"{remaining:g}%" + (" ※" if old else ""),
                             fg=ACCENT if remaining > 30 else (WARN if remaining > 10 else DANGER))
            when = datetime.fromtimestamp(window["reset"]).strftime("%m/%d %H:%M")
            reset.configure(text=f"リセット {when}" + (" · 要更新" if expired else ""))
            if slot == "secondary":
                count = runs.get("runs")
                badge.configure(text=f"5h枠 あと{count}回" if count is not None else "5h枠 あと—回")
            else:
                badge.configure(text="")
            self.paint_bar(slot)
        selected = self.table.selection()
        self.table.delete(*self.table.get_children())
        self.rows = {}
        self.orchestration_rows = {}
        self.delegation_rows = {}
        delegated = self.display_mode.get() == "委任の実績"
        combined = self.display_mode.get() == "全体・構成"
        self.table.configure(displaycolumns=self.delegation_columns if delegated else self.usage_columns)
        self.table_note.configure(text="通常作業・同じ条件ごと" if delegated else "連続使用時の目安")
        self.table.heading("model", text="構成 / 推論 ×人数" if combined else "モデル / 推論")
        self.table.heading("tokens", text="区間" if combined else "トークン")
        if delegated:
            self.render_delegation()
        elif combined:
            self.render_orchestration(bucket, now)
        active_models = {key for key, _ in self.view.get("orchestration", {}).get("active", ())}
        models = order_models(self.view["models"], active_models) if not combined and not delegated else []
        for index, model in enumerate(models):
            results = [self.last_forecasts.present(("model", model, bucket, slot),
                       forecast(self.view, model, bucket, slot, now)) for slot in ("primary", "secondary")]
            measured = max((self.view["aggregates"].get((model, bucket, s), {}).get("seconds", 0)
                            for s in ("primary", "secondary")), default=0)
            measured = max(measured, results[0].get("seconds", 0))
            rate = rate_text(results[0])
            iid = f"model-{index}"
            self.rows[iid] = (model, results)
            self.table.insert("", "end", iid=iid, values=(f"{model_text(model[0])} {effort_text(model[1])}", effort_text(model[1]),
                              *(forecast_text(r) for r in results), f"{measured / 60:.0f}分",
                              f"{self.view.get('usage', {}).get(model, {}).get('total_tokens', 0):,}", rate,
                              full_text(results[0]) if "full_hours" in results[0] else
                              forecast_text(full_forecast(self.view, model, bucket))),
                              tags=("current",) if model in active_models else ())
        if selected and (selected[0] in self.rows or selected[0] in self.orchestration_rows or selected[0] in self.delegation_rows):
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
            self.status.configure(text="トークン記録中 · 利用枠は未取得" if self.view.get("usage") else "使用量の記録待ち")
        if delegated:
            data = self.delegation
            if data["status"] in ("loading", "missing"):
                text = "委任記録を確認中…" if data["status"] == "loading" else "委任記録はまだありません"
            else:
                text = f"通常作業 {data['count']}件 · 合格 {data['passes']}件 · 手直し {data['rework']}件"
                text += f" · 実行中 {data['pending']}件 · 初期設定等の除外 {data['excluded']}件"
                if data["errors"]:
                    text += f" · 読取不完全 {data['errors']}件"
            self.status.configure(text=text)
        if self.tray:
            result = self.last_forecasts.present(("composition", None, bucket, "primary"),
                     orchestration_forecast(self.view, bucket, now=now, recent=True))
            icon_status = ("stale" if result.get("historical") and result["status"] != "exhausted"
                           else result["status"])
            signature = (time_label(result), icon_status)
            if signature != self.last_icon_signature:
                self.tray.icon = make_icon(*signature)
                self.last_icon_signature = signature
            short = point.get("primary")
            weekly = point.get("secondary")
            parts = [TITLE, f"全体: {forecast_text(result)}"]
            if previous_note(result):
                parts.append(previous_note(result))
            parts.append(credits_text(self.credits, now))
            for label, w in (("短期", short), ("週間", weekly)):
                if w:
                    if now < w["reset"]:
                        wait = max(0, int((w["reset"] - now) / 60))
                        hours, mins = divmod(wait, 60)
                        parts.append(f"{label}: 残り{100 - w['used']:g}% · 更新まで{hours}h{mins:02d}m")
                    else:
                        parts.append(f"{label}: 前回{100 - w['used']:g}% · 更新待ち")
            if weekly and runs.get("runs") is not None:
                parts[-1] += f" · 5h枠あと{runs['runs']}回"
            parts.append(composition_text(self.view.get("orchestration", {}).get("active", ())))
            if stamp and now - stamp > 300:
                parts.append("過去の観測値")
            title = "\n".join(parts)
            if sys.platform == "win32":
                # Windowsのツールチップ長に合わせてUTF-16で切り詰める。
                title = title.encode("utf-16-le")[:254].decode("utf-16-le", errors="ignore")
            self.tray.title = title
            self.check_notifications(point, now)
        self.last_forecasts.save()
        self.selection()

    def render_delegation(self):
        for group in self.delegation["groups"]:
            iid = "delegation-" + group["id"]
            model, _, effort = group["model"].rpartition(":")
            values = [""] * 8 + [profile_text(group["profile"]),
                      f"{model_text(model)} {effort_text(effort)}", group["count"],
                      f"{group['passes']}/{group['count']}", group["rework"],
                      f"{group['median_tokens']:,.0f}" if group["median_tokens"] is not None else "未取得",
                      duration(group["median_seconds"] / 3600) if group["median_seconds"] is not None else "未取得",
                      "参考比較" if group["comparison"] and group["comparison"]["ready"] else "記録不足"]
            self.table.insert("", "end", iid=iid, values=values)
            self.delegation_rows[iid] = group
        if not self.table.selection() and self.delegation_rows:
            self.table.selection_set(next(iter(self.delegation_rows)))

    def delegation_detail(self, group):
        risk = {"low": "低", "medium": "中", "high": "高"}[group["risk"]]
        parts = [f"{group['model']} · リスク{risk} · 手順 {group['revision'][:8]}",
                 f"合格率 {100 * group['passes'] / group['count']:.0f}% · 手直し率 {100 * group['rework'] / group['count']:.0f}%"]
        observed = []
        for key, label in (("median_parent", "親"), ("median_child", "子")):
            value = group[key]
            observed.append(f"{label} {value:,.0f}" if value is not None else f"{label} 未取得")
        parts.append("トークン中央値：" + "／".join(observed) + f"（両者取得 {group['observed_count']}/{group['count']}件）")
        comparison = group["comparison"]
        if comparison and comparison["ready"]:
            parts.append(f"参考比較：変更前{comparison['before']['count']}件→後{comparison['after']['count']}件")
            parts.append(f"合格率 {comparison['pass_change']:+.0f}pt · 手直し率 {comparison['rework_change']:+.0f}pt")
            for key, label in (("token_change", "トークン"), ("time_change", "時間")):
                value = comparison[key]
                parts.append(f"{label} {value:+.1f}%" if value is not None else f"{label}比較は記録不足")
            parts.append("同分類・同リスクの観測比較。作業難易度の差は未補正")
        else:
            parts.append("変更前後の比較は記録不足（確認済みの変更と前後各3件が必要）")
        parts.append("トークン・時間は中央値。課金額ではありません")
        return " / ".join(parts)

    def render_orchestration(self, bucket, now):
        data = self.view.get("orchestration", {})
        active = data.get("active", ())
        signatures = {key[0] for key in data.get("aggregates", {}) if key[1] == bucket}
        if active:
            signatures.add(active)
        current = ("構成未確認" if self.view.get("incomplete") else
                   composition_text(active) if active else "稼働なし")
        rows = [("recent", None, "全体：" + current)]
        rows.extend((f"composition-{index}", signature,
                     ("現在: " if signature == active else "") + composition_text(signature))
                    for index, signature in enumerate(sorted(signatures, key=lambda s: (s != active, s))))
        for iid, signature, label in rows:
            results = [self.last_forecasts.present(("composition", signature, bucket, slot),
                       orchestration_forecast(self.view, bucket, slot, now,
                       signature=signature, recent=signature is None)) for slot in ("primary", "secondary")]
            agg = (data.get("recent", {}).get((bucket, "primary"), {}) if signature is None else
                   data.get("aggregates", {}).get((signature, bucket, "primary"), {}))
            full = full_text(results[0])
            rate = rate_text(results[0])
            self.orchestration_rows[iid] = (signature, results)
            self.table.insert("", "end", iid=iid, values=(label, "", forecast_text(results[0]),
                              forecast_text(results[1]), f"{agg.get('seconds', 0) / 60:.0f}分",
                              str(agg.get("samples", 0)), rate, full),
                              tags=("current",) if signature == active or signature is None else ())
        if not self.table.selection():
            self.table.selection_set("recent")

    def selection(self, _=None):
        selected = self.table.selection()
        if selected and selected[0] in self.delegation_rows:
            self.detail.configure(text=self.delegation_detail(self.delegation_rows[selected[0]]))
            return
        if self.display_mode.get() == "委任の実績":
            self.detail.configure(text="通常作業を記録すると表示します。初期設定・比較試験・未分類は集計対象外です。")
            return
        if selected and selected[0] in self.orchestration_rows:
            signature, results = self.orchestration_rows[selected[0]]
            parts = []
            if signature is None:
                active = self.view.get("orchestration", {}).get("active", ())
                current = ("構成未確認" if self.view.get("incomplete") else
                           composition_text(active, full=True) if active else "なし")
                parts.append("稼働中：" + current)
            for label, result in zip(("短期", "週間"), results):
                text = f"{label}: {forecast_text(result)}"
                if "rate" in result:
                    text += f" · {result['rate']:.2f}%/時"
                    if result.get("reset_first"):
                        text += " · リセットが先"
                parts.append(text)
                if previous_note(result):
                    parts.append(previous_note(result))
            if signature is None:
                parts.append("最終観測までの15分の活動区間。構成変更を含むペース")
            else:
                parts.insert(0, composition_text(signature, full=True))
                parts.append("同じモデル・推論・人数の全期間平均。親子・別チャットを含む")
            if self.view.get("incomplete"):
                parts.append("読取不足のため予測停止")
            self.detail.configure(text=" / ".join(parts))
            return
        if not selected or selected[0] not in self.rows:
            self.detail.configure(text="")
            return
        model, results = self.rows[selected[0]]
        texts = [f"{model[0]} / {model[1]}"]
        for label, slot in (("100%使用時間", "primary"), ("週間100%使用時間", "secondary")):
            full = full_forecast(self.view, model, self.bucket.get(), slot)
            if full["status"] == "ready":
                texts.append(f"{label}: {duration(full['hours'])}")
        for name, result in zip(("短期", "週間"), results):
            if "rate" in result:
                if previous_note(result):
                    texts.append(previous_note(result))
                if result.get("credit"):
                    texts.append(f"OpenRouter: ${result['dollars']:.4f}/時間 · 計測開始残高に対する {result['rate']:.2f}%/時間")
                    continue
                text = f"{name}: {result['rate']:.1f}%/時間 · {duration(result['low'])}〜{duration(result['high'])}"
                if result["reset_first"]:
                    text += " · リセットが先"
                texts.append(text)
        usage = self.view.get("usage", {}).get(model)
        if usage:
            texts.append(f"記録分: 入力 {usage.get('input_tokens', 0):,} · キャッシュ {usage.get('cached_input_tokens', 0):,} · 出力 {usage.get('output_tokens', 0):,}")
        if not model[0].startswith("openrouter/") and results[1]["status"] != "unavailable":
            texts.append(f"週間枠: {forecast_text(results[1])}")
        self.detail.configure(text=" / ".join(texts) if texts else f"{model[0]} · 推定に必要な単独使用の記録を収集中")

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
                self.error_label.configure(text="")
                changed = True
            elif kind == "delegation":
                self.delegation = value
                changed = True
            elif kind == "credits":
                if value["status"] == "ready":
                    self.credits = value
                else:
                    self.credits = {**self.credits, "status": value["status"]}
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
                self.error_label.configure(text="⚠ エラー")
                self.busy = False
                self.refresh_button.configure(state="normal")
        if self.render_due(changed):
            self.render()
        if self.tray_available:
            for notice in self.delegation_notices.update(self.delegation):
                try:
                    self.tray.notify(notice["text"], TITLE)
                except Exception:
                    pass
        self.dismiss_if_unfocused()
        self.root.after(500, self.tick)

    def hide(self):
        if self.tray_available:
            self.root.withdraw()
        else:
            self.quit()

    def show(self):
        self.root.deiconify()
        neutral_title(self.root)
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
        self.wake.set()
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
    enable_dpi_awareness()
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
