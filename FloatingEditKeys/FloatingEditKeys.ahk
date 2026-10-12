#Requires AutoHotkey v2.0
#SingleInstance Force
CoordMode("Mouse", "Screen")
; ── レイアウト定数 ─────────────────────────────────
pad  := 2
gap  := 1
btnH := 18
btnW := 56
W    := pad*2 + btnW
H    := pad*2 + btnH*4 + gap*3
; ── マウスのいるモニタ右下に配置 ───────────────────
MouseGetPos &mx, &my
mon := GetMonitorFromPoint(mx, my)
MonitorGetWorkArea(mon, &L, &T, &R, &B)
x := Clamp(R - W - 20, L, R - W)
y := Clamp(B - H - 20, T, B - H)
; ── GUI本体 ────────────────────────────────────────
gui1 := Gui("+AlwaysOnTop +ToolWindow -Caption +E0x08000000 +E0x00080000", "FloatingEditKeys")
gui1.BackColor := "070A0E"

; ── Enter ───────────────────────────────────────────
labelEnter := gui1.AddText(
    "x" pad " y" pad " w" btnW " h" btnH " +0x200 Center Background11171E",
    "ENTER")
labelEnter.SetFont("s7 Bold cE4E7EB", "Segoe UI")
labelEnter.OnEvent("Click", (*) => Send("{Enter}"))

; ── Backspace ───────────────────────────────────────
yBack := pad + btnH + gap
labelBack := gui1.AddText(
    "x" pad " y" yBack " w" btnW " h" btnH " +0x200 Center Background11171E",
    "BACK")
labelBack.SetFont("s7 Bold cE4E7EB", "Segoe UI")
labelBack.OnEvent("Click", (*) => Send("{Backspace}"))

; ── Delete ──────────────────────────────────────────
yDelete := pad + btnH*2 + gap*2
labelDelete := gui1.AddText(
    "x" pad " y" yDelete " w" btnW " h" btnH " +0x200 Center Background11171E",
    "DELETE")
labelDelete.SetFont("s7 Bold cE4E7EB", "Segoe UI")
labelDelete.OnEvent("Click", (*) => Send("{Delete}"))

; ── Window switch ──────────────────────────────────
yWindow := pad + btnH*3 + gap*3
labelWindow := gui1.AddText(
    "x" pad " y" yWindow " w" btnW " h" btnH " +0x200 Center Background11171E",
    "WINDOW")
labelWindow.SetFont("s7 Bold cE4E7EB", "Segoe UI")
labelWindow.OnEvent("Click", (*) => CycleWindows())

; ── ホバー状態 ─────────────────────────────────────
global gControlRows := Map(
    labelEnter.Hwnd, "enter",
    labelBack.Hwnd, "back",
    labelDelete.Hwnd, "delete",
    labelWindow.Hwnd, "window")
global gRowControls := Map(
    "enter", labelEnter,
    "back", labelBack,
    "delete", labelDelete,
    "window", labelWindow)
global gHoveredRow := ""
; ── 半透明・表示 ───────────────────────────────────
global gGuiHwnd := gui1.Hwnd
WinSetTransparent(248, gui1)
OnMessage(0x0204, WM_RBUTTONDOWN)
gui1.Show("Hide x" x " y" y " w" W " h" H)
SetRoundedCorners(gui1.Hwnd)
WinSetAlwaysOnTop(1, gui1)
KeepWidgetOnTop()
SetTimer(KeepWidgetOnTop, 500)
SetTimer(UpdateHoverState, 40)

; ── 右ボタン長押しでカーソル付近へ呼び出し ────────
; 短い右クリックは通常どおり送出する。
; 左ボタンを押している間は無効にし、既存の左ホールド＋右ドラッグを残す。
#HotIf !GetKeyState("LButton", "P")
$RButton:: {
    if KeyWait("RButton", "T0.5") {
        Click("Right")
        return
    }

    MoveWidgetNearCursor()
    KeyWait("RButton")
}
#HotIf

; ════════════════════════════════════════════════════
CycleWindows() {
    static cycle := []

    currentWindows := GetSwitchableWindows()
    if !SameWindowSet(cycle, currentWindows)
        cycle := currentWindows
    if (cycle.Length = 0)
        return

    activeHwnd := WinExist("A")
    activeIndex := 0
    for index, hwnd in cycle {
        if (hwnd = activeHwnd) {
            activeIndex := index
            break
        }
    }

    targetIndex := Mod(activeIndex, cycle.Length) + 1
    targetHwnd := cycle[targetIndex]
    try {
        if (WinGetMinMax("ahk_id " targetHwnd) = -1)
            WinRestore("ahk_id " targetHwnd)
        WinActivate("ahk_id " targetHwnd)
    }
}

GetSwitchableWindows() {
    global gGuiHwnd
    windows := []

    for hwnd in WinGetList() {
        if (hwnd = gGuiHwnd || !DllCall("IsWindowVisible", "ptr", hwnd))
            continue

        try {
            title := WinGetTitle("ahk_id " hwnd)
            className := WinGetClass("ahk_id " hwnd)
            exStyle := WinGetExStyle("ahk_id " hwnd)
        } catch {
            continue
        }

        if (title = "" || exStyle & 0x80)
            continue
        if (className = "Shell_TrayWnd"
            || className = "Progman"
            || className = "WorkerW"
            || className = "Windows.UI.Core.CoreWindow")
            continue

        cloaked := 0
        try DllCall("dwmapi\DwmGetWindowAttribute"
            , "ptr", hwnd
            , "uint", 14
            , "uint*", &cloaked
            , "uint", 4)
        if cloaked
            continue

        owner := DllCall("GetWindow", "ptr", hwnd, "uint", 4, "ptr")
        if (owner && !(exStyle & 0x40000))
            continue

        windows.Push(hwnd)
    }
    return windows
}

SameWindowSet(first, second) {
    if (first.Length != second.Length)
        return false

    for hwnd in first {
        found := false
        for otherHwnd in second {
            if (hwnd = otherHwnd) {
                found := true
                break
            }
        }
        if !found
            return false
    }
    return true
}

UpdateHoverState() {
    global gGuiHwnd, gControlRows

    MouseGetPos(,, &windowUnderMouse, &controlUnderMouse, 2)
    row := ""
    if (windowUnderMouse = gGuiHwnd && gControlRows.Has(controlUnderMouse))
        row := gControlRows[controlUnderMouse]
    SetHoveredRow(row)
}

SetHoveredRow(row) {
    global gHoveredRow, gRowControls

    if (row = gHoveredRow)
        return
    gHoveredRow := row

    for rowName, control in gRowControls {
        isHovered := (rowName = row)
        background := isHovered ? "2A3543" : "11171E"
        labelColor := isHovered ? "FFFFFF" : "E4E7EB"

        control.Opt("Background" background)
        control.SetFont("c" labelColor)
        control.Redraw()
    }
}

SetRoundedCorners(hwnd) {
    ; Windows 11 のDWM角丸を使う。表示領域を切り抜かないため、
    ; 画面拡大率が変わっても右端のDeleteが欠けない。
    try DllCall("dwmapi\DwmSetWindowAttribute"
        , "ptr", hwnd
        , "uint", 33
        , "int*", 2
        , "uint", 4)
}

MoveWidgetNearCursor() {
    global gui1, W, H

    MouseGetPos(&mx, &my)
    mon := GetMonitorFromPoint(mx, my)
    MonitorGetWorkArea(mon, &L, &T, &R, &B)

    offset := 16
    margin := 8
    widgetW := W
    widgetH := H

    ; カーソルのX座標とEnter段の中央を揃える。
    x := Round(mx - widgetW / 2)

    ; 下に収まらない場合は、画面端へ飛ばさずカーソルの上へ置く。
    y := (my + offset + widgetH <= B - margin)
        ? my + offset
        : my - offset - widgetH

    x := Clamp(x, L + margin, R - widgetW - margin)
    y := Clamp(y, T + margin, B - widgetH - margin)
    gui1.Show("NA x" x " y" y " w" W " h" H)
    KeepWidgetOnTop()
    SetTimer(HideWidget, 0)
    SetTimer(HideWidget, -10000)
}

HideWidget() {
    global gui1
    gui1.Hide()
}

KeepWidgetOnTop() {
    global gGuiHwnd
    static HWND_TOPMOST := -1
    static SWP_FLAGS := 0x0001 | 0x0002 | 0x0010 | 0x0200

    DllCall("SetWindowPos"
        , "ptr", gGuiHwnd
        , "ptr", HWND_TOPMOST
        , "int", 0
        , "int", 0
        , "int", 0
        , "int", 0
        , "uint", SWP_FLAGS)
}
WM_RBUTTONDOWN(wParam, lParam, msg, hwnd) {
    global gGuiHwnd
    if (DllCall("GetAncestor", "ptr", hwnd, "uint", 2, "ptr") = gGuiHwnd)
        DragWindow(gGuiHwnd)
}
DragWindow(hwnd) {
    DllCall("ReleaseCapture")
    DllCall("SendMessage", "ptr", hwnd, "uint", 0x00A1, "ptr", 2, "ptr", 0)
}
Clamp(v, lo, hi) => (v < lo) ? lo : (v > hi) ? hi : v
GetMonitorFromPoint(x, y) {
    Loop MonitorGetCount() {
        MonitorGet(A_Index, &L, &T, &R, &B)
        if (x >= L && x < R && y >= T && y < B)
            return A_Index
    }
    return 1
}
