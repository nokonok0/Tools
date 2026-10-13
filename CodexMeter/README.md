# Codex Meter

Windows tray application showing observed Codex quotas and empirical per-model runway estimates.

## Run

Requires Windows, Python 3.11+ and uv. From this directory run `uv sync --locked --python 3.11` once to create the isolated `.venv` from `uv.lock`, then double-click `CodexMeter.pyw`. The launcher uses that environment automatically without running uv or installing packages at each launch. It starts in the system tray without a taskbar window. Hover for remaining quotas, reset countdowns and the latest model's short-term estimate. Click to toggle a compact panel near the taskbar on the current monitor. Clicking outside the panel, Escape or closing it hides it. Right-click for Show/Hide, Refresh and Quit. A second launch opens the existing panel through a named event. Without pystray, the window still works and closing it exits.

To start automatically at Windows sign-in, run `powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Install-Startup.ps1` after setup. It creates a per-user `Codex Meter.lnk` startup shortcut pointing directly to `.venv\Scripts\pythonw.exe`, so no terminal window or package synchronization runs during sign-in. After moving this directory, rerun the installer to update the shortcut. Delete that shortcut from the Windows Startup folder to disable automatic launch.

Fresh remaining quotas at 20% and 10% trigger a tray notification, at most once per threshold and reset window. Notification deduplication is stored in `%LOCALAPPDATA%\CodexMeter\notifications.json`. Windows notification settings control whether notifications are visible. Stale or expired observations do not notify.

## Data and estimates

- Reads the existing Codex session logs in `%CODEX_HOME%\sessions` or `%USERPROFILE%\.codex\sessions`. No API key, network calls, credential reads, CLI launches, MCP servers or inference requests.
- Quotas are account/bucket-wide, not a separate balance for every model. Estimates show how long the latest shared balance would last if only that model and reasoning setting were used continuously at its observed rate.
- Initial reads are bounded to the last 1 MiB of each of at most 64 recently modified logs; subsequent polls read only appended bytes, at most 2 MiB per file per poll. Polls run every 10 seconds in a background thread; file discovery runs once a minute.
- Per-model attribution requires exactly one known active turn throughout the observation interval. Mixed or concurrent turns, unknown model contexts, gaps over 180 seconds, quota resets, decreases, expired windows and missing data are excluded. If the 64-file discovery limit is exceeded, prediction is disabled rather than attributing incomplete data.
- A rate requires at least 3 minutes, 5 intervals and 3 percentage points of observed consumption. Zero-change intervals are included. The displayed range accounts only for rounded quota percentages; changing task complexity can cause larger variation. Estimates are empirical, not guarantees.
- An uncompleted/abandoned task is considered inactive three minutes after its last observed event. New activity extends that bound. Other devices and cloud work are not observable from these local logs, so their account-wide consumption can affect attribution and estimates.
- Usage logs update during Codex activity. After five minutes without a quota observation, predictions show Update Pending. Passed reset times also show Update Pending; the app does not guess a new quota balance.
- Only whitelisted model names, reasoning levels, timestamps, task boundaries, bucket identifiers and quota counters are persisted in `%LOCALAPPDATA%\CodexMeter\observations.json`. No conversation, code, paths, credentials or account IDs are stored. Session file paths are hashed. Retention is seven days or 24,000 events. Writes are atomic and occur at most every two minutes, plus exit.
- Raw logs may contain conversations, but parsing retains only the listed structured metadata. Existing Codex files are never edited.
- A missing short-term or weekly bucket stays unavailable. Custom-provider usage without Codex quota snapshots is not estimated.

Run the focused checks with `uv run --locked python -m unittest discover -s tests`.

Protocol reference: https://learn.chatgpt.com/docs/app-server (account/rateLimits/read, quota window semantics).
