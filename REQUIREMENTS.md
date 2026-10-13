# Tools Requirements

## Repository

- Group small Windows tools in separate directories with a root tool index.
- Preserve the existing FloatingEditKeys Git history.
- Publish application source, documentation and focused tests only.
- Keep editable/private runtime data, credentials, observations and notification history outside version control.

## FloatingEditKeys

- Remain normally hidden; hold the right mouse button for at least 0.5 seconds to show it near the pointer for 10 seconds.
- Preserve ordinary short right-click behavior.
- Provide Enter, Backspace, Delete and window cycling controls.
- Keep the compact vertical layout and constrain placement to the current monitor work area.
- Maintain the user's target focus when showing the widget while allowing subsequent target changes.
- Installation and runtime remain independent of Codex Meter.

## Codex Meter

- Read observed account-wide Codex quotas, reset times, models and reasoning settings from local session logs.
- Predict remaining continuous-use time per model and reasoning setting from observed quota consumption, rather than assigning invented independent model quotas.
- Exclude ambiguous/mixed/concurrent intervals, resets, decreasing counters and stale data from prediction. Display learning/update-pending states when appropriate.
- Show the empirical time used for calculation and label the short-term forecast as estimated time until the limit.
- Start hidden in the Windows tray, with no ordinary taskbar window.
- Show remaining quotas, reset countdowns and the latest model estimate on hover.
- Toggle a compact panel on tray click; dismiss on outside focus, Escape or Close.
- Keep panel placement within the current monitor work area. Do not use always-on-top behavior.
- Notify at 20% and 10% remaining once per threshold and reset window; never notify from stale observations.
- A repeated launch opens the existing panel rather than creating a duplicate process.
- Manage dependencies with uv and a committed lockfile in an ignored per-app `.venv`; normal double-click launch uses that environment without package installation.
- Support automatic Windows sign-in startup through a per-user shortcut targeting the virtual environment's `pythonw.exe`, with no terminal window.
- Read bounded recent log tails initially and only appended bytes afterward. Persist only whitelisted observations, for seven days or 24,000 events, at most every two minutes plus exit.
- Keep Codex files read-only and use no network requests, credential reads, inference calls, CLI launches or MCP launches at runtime.

## Codex Auto

- Provide a `codex-auto` command that classifies the initial request locally with an installed Ollaya model and launches the ordinary Codex CLI.
- Select a model and supported reasoning setting from the local Codex catalog; preserve explicit user overrides.
- Route uncertain, high-impact, truncated or failed judgments toward a stronger available model. Do not automatically download decision models.
- Select once at startup. Preserve the user's terminal, current directory, approval/sandbox settings and child exit status.
- Pass requests as literal argv values with no shell interpolation. Store no request or decision history.
- Offer dry-run, JSON output without the request, model listing, and native Codex option passthrough after `--`.
- Display fresh Codex Meter quota observations when available; do not reduce difficult-task quality solely because quota is low.
- Limit Ollaya connections to loopback, disable proxies/redirects, and start a missing local server without visible console windows.
- Install with uv in an isolated tool environment.

## Changes

- 2026-10-13: Rename the FloatingEditKeys repository to Tools and add Codex Meter as a separate tool. Carry forward existing FloatingEditKeys source and documentation unchanged.
- 2026-10-13: Add uv-managed isolated dependencies and a Windows sign-in startup installer for Codex Meter.
- 2026-10-13: Add Codex Auto, an Ollaya-based initial-request model selector for Codex CLI.
- 2026-10-13: Preserve the standard npm Codex launcher and verify literal argument handoff; document UTF-8 pipeline input for Windows PowerShell.
