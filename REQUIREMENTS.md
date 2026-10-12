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
- Read bounded recent log tails initially and only appended bytes afterward. Persist only whitelisted observations, for seven days or 24,000 events, at most every two minutes plus exit.
- Keep Codex files read-only and use no network requests, credential reads, inference calls, CLI launches or MCP launches at runtime.

## Changes

- 2026-10-13: Rename the FloatingEditKeys repository to Tools and add Codex Meter as a separate tool. Carry forward existing FloatingEditKeys source and documentation unchanged.
