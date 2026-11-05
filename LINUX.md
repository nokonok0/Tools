# Linux で動かす場合

このリポジトリはWindowsを既定にしています。**Windows向けの手順・CI・`uv.lock`・`%LOCALAPPDATA%`の保存先は変えていません。** ここに書くのは、Linuxで動かすときだけに必要な追加手順です。

対象はUbuntu 24.04 + GNOME（Wayland）で確認しています。デスクトップのトレイはGNOMEのAppIndicators拡張を使います。

## 競合しないための分け方

| 項目 | Windows | Linux |
| --- | --- | --- |
| 仮想環境 | `.venv`（`uv sync --locked`） | `.venv-linux`（`--system-site-packages`） |
| 依存の解決 | `uv.lock` | `pip`で`pyproject.toml`と同じ下限を導入 |
| 保存先 | `%LOCALAPPDATA%\CodexMeter` など | `~/.local/share/CodexMeter` など |
| トレイ | pystrayのwin32バックエンド | pystrayのAppIndicatorバックエンド |
| 自動起動 | スタートアップフォルダ（`tools\Install-Startup.ps1`） | `~/.config/autostart` |

同じフォルダを2つのOSで共有しても、`.venv`と`.venv-linux`、保存先が別なので取り違えません。Linux用のファイルは`.gitignore`で除外しています。

Linuxでは`uv sync --locked`を使わないでください。`uv.lock`はWindows専用として解決されているため失敗し、`.venv`を作り直そうとします。Windows側の作業は従来どおり`uv sync --locked --python 3.11`です。

## 導入手順

```bash
tools/install-linux.sh
```

次の順に処理します。追加の常駐プロセスやcronは作りません。

1. `.venv-linux`を`python3.12 --system-site-packages`で作成（PyGObjectをシステムから使うため）
2. `pillow`・`pystray`（DeepSeek Meterは`zstandard`も）を導入
3. AppIndicatorのtypelibを`~/.local/share/meter/typelib`へ配置（sudo不要）
4. `~/.local/bin/{codex,deepseek}-meter`、アプリ一覧、`~/.config/autostart`を登録

自動起動を登録しない場合は`tools/install-linux.sh --no-autostart`、削除は`tools/install-linux.sh --uninstall`です。保存データは削除しません。

手動で動かす場合（uvがある環境）:

```bash
cd CodexMeter
uv venv --python 3.12 --system-site-packages .venv-linux
uv pip install --python .venv-linux/bin/python "pillow>=13.0.0" "pystray>=0.19.5,<0.20"
GI_TYPELIB_PATH="$HOME/.local/share/meter/typelib" .venv-linux/bin/python CodexMeter.pyw
```

uvがなければ`python3.12 -m venv --system-site-packages .venv-linux`を使います（`sudo apt install python3.12-venv`が必要な場合があります）。DeepSeek Meterは`zstandard>=0.23.0`も必要です。

## 仕組み

- `CodexMeter.pyw`と`DeepSeekMeter.pyw`は、Linuxでは`.venv-linux/bin/python`で自分自身を起動し直します。Windowsでは従来どおり`.venv\Scripts\pythonw.exe`を使います。
- 保存先は`os.name`で分けます。Windowsは`LOCALAPPDATA`、Linuxは`XDG_DATA_HOME`（未設定なら`~/.local/share`）です。
- トレイのツールチップは、WindowsだけUTF-16で長さを切り詰めます。Windowsのツールチップ上限に合わせる処理で、Linuxでは文字列のまま渡します。
- 通常／軽量の共通設定は、両OSとも`MeterCommon/runtime.json`です（保存先の親だけが異なります）。

## 制限

- トレイはGNOMEのAppIndicators拡張が有効な環境で表示されます。拡張がない場合はアイコンが出ません。
- `gir1.2-ayatanaappindicator3-0.1`がシステムに入っていれば、typelibのローカル配置は不要です。
- DDC/CIを使う Monitor Brightness Sync（C#）とFloatingEditKeys（AutoHotkey）はWindows専用で、Linuxでは動きません。
- Linuxでは二重起動の防止とパネルのタスクバー付近への配置は行いません（Windowsのmutexと`MonitorFromPoint`はWindows専用のため）。

## Windows側への影響

Linux対応で変えたのは次の点だけで、いずれもWindowsでは従来の分岐を通ります。

- 保存先の決定（`os.name == "nt"`のときは従来の式のまま）
- 起動スクリプトの仮想環境の選択（Windowsは`.venv\Scripts\pythonw.exe`のまま）
- ツールチップの切り詰め（Windowsのみ従来の処理）

WindowsのテストとCIはこの追加の影響を受けません。
