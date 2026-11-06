"""Meter共通の配色と、高DPI画面向けのネイティブ表示。"""
import ctypes
import sys
from tkinter import ttk

BG = "#eef1f5"
PANEL = "#f8fafc"
INK = "#26313b"
MUTED = "#63717e"
ACCENT = "#278c87"
LINE = "#d2dae3"
SELECTED = "#d9ecec"
FONT = "Yu Gothic UI"


def enable_dpi_awareness():
    # Tk生成前に指定し、Windowsによるウィンドウ全体の拡大補間を防ぐ。
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def pixels(root, value):
    return round(value * root.winfo_fpixels("1i") / 96)


def apply_theme(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=INK, font=(FONT, 9))
    style.configure("Muted.TLabel", foreground=MUTED, font=(FONT, 8))
    style.configure("Title.TLabel", font=("Segoe UI Semibold", 10), foreground=INK)
    style.configure("Number.TLabel", font=("Segoe UI Semibold", 12), foreground=INK)
    style.configure("Dot.TLabel", foreground=ACCENT, font=(FONT, 10))
    style.configure("Credit.TLabel", foreground=MUTED, font=(FONT, 8))
    style.configure("Error.TLabel", foreground="#b94b50", font=(FONT, 8))
    style.configure("TButton", font=(FONT, 8), padding=(6, 2), relief="flat",
                    background=PANEL, foreground=INK, borderwidth=1,
                    bordercolor=LINE, lightcolor=PANEL, darkcolor=PANEL)
    style.map("TButton", background=[("active", SELECTED)],
              foreground=[("disabled", MUTED)])
    style.configure("TCombobox", font=(FONT, 8), padding=(4, 1),
                    background=PANEL, fieldbackground=PANEL, foreground=INK,
                    arrowcolor=MUTED, bordercolor=LINE, lightcolor=PANEL, darkcolor=PANEL)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
              selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", INK)])
    root.option_add("*TCombobox*Listbox.font", (FONT, 9))
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECTED)
    root.option_add("*TCombobox*Listbox.selectForeground", INK)
    style.configure("Treeview", font=(FONT, 9), rowheight=pixels(root, 24),
                    background=PANEL, fieldbackground=PANEL, foreground=INK,
                    borderwidth=0)
    style.configure("Treeview.Heading", font=(FONT, 8), background=BG,
                    foreground=MUTED, relief="flat", padding=(4, 5))
    style.map("Treeview.Heading", background=[("active", LINE)])
    style.map("Treeview", background=[("selected", SELECTED)],
              foreground=[("selected", INK)])
    for name in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(name, background=LINE, troughcolor=BG, arrowcolor=MUTED,
                        bordercolor=BG, lightcolor=LINE, darkcolor=LINE,
                        arrowsize=pixels(root, 10), width=pixels(root, 10))
    return style
