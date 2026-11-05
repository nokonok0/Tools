# Tools

Windowsで日常的に使う小さなツールをまとめたリポジトリです。

| ツール | 用途 | 起動・導入 |
| --- | --- | --- |
| [FloatingEditKeys](FloatingEditKeys/) | マウスの右長押しで呼び出す編集キーウィジェット | [操作・導入方法](FloatingEditKeys/README.md) |
| [Codex Meter](CodexMeter/) | Codexの残量と、モデル・推論設定別の上限までの推定時間を表示するトレイアプリ | [起動・計算方法](CodexMeter/README.md) |
| [DeepSeek Meter](DeepSeekMeter/) | DeepSeekの残高と枯渇までの推定時間、モデル別トークンを表示するトレイアプリ | [起動・計算方法](DeepSeekMeter/README.md) |
| [Monitor Brightness Sync](MonitorBrightnessSync/) | 内蔵画面に外部モニターの明るさを追従させる常駐ツール | [設定・ビルド](MonitorBrightnessSync/README.md) |

各ツールは個別に起動できます。必要な環境・導入手順・操作方法は、一覧の個別READMEを参照してください。

Codex MeterとDeepSeek Meterは、パネルまたはトレイから「通常／軽量」を一括切替できます。共通コードの`MeterCommon/`を各Meterと同じ親フォルダに置いてください。

## プラットフォーム

Windowsを既定とします。Windows向けの手順・CI・`uv.lock`・保存先（`%LOCALAPPDATA%`）は変えていません。Linux（Ubuntu/GNOME）で動かす場合だけ[LINUX.md](LINUX.md)を参照してください。

Linux用の環境は`.venv-linux`、保存先は`~/.local/share`に分けているため、同じフォルダを2つのOSで共有してもWindows側と取り違えません。Monitor Brightness SyncとFloatingEditKeysはWindows専用です。

## 個人データ

公開対象はコード・説明書・テストです。Codex・DeepSeekの会話、認証情報、使用量履歴、通知履歴を含めません。

## 開発

変更の確認手順は各ツールのREADMEを参照してください。機能・使い方の変更は各ツールのREADMEと[REQUIREMENTS.md](REQUIREMENTS.md)に反映してください。

GitHubへ反映すると、mainへのpush・プルリクエスト・手動実行・毎週月曜日の日本時間9時に、Windows上でCodex MeterとDeepSeek Meterの単体テストと依存ライブラリの脆弱性検査を行います。既知の脆弱性が見つかると検査を失敗させます。Actionsの参照先はコミットSHAに固定し、実行権限はリポジトリの読み取りだけに制限します。

DependabotはCodex MeterとDeepSeek Meterの依存ライブラリ・ロックファイルとGitHub Actionsの更新を毎週確認し、更新用プルリクエストを作ります。自動マージは行いません。ブランチ保護と必須チェックの設定はGitHub側で別途行う必要があります。
