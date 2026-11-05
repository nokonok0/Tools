# 説明文の言語

- README、要件書、ツールの説明、ヘルプ、ユーザー向けのメッセージは日本語を既定にする。
- 固有名詞、コマンド、API名、コードの識別子、外部ソフト自身の表示は原表記を維持する。
- 変更時は関連する説明書と`REQUIREMENTS.md`を更新する。個人データや認証情報は公開しない。

# プラットフォーム

- Windowsを既定とする。Linux対応の変更でも、Windowsの手順・CI・`uv.lock`・`%LOCALAPPDATA%`の保存先・起動方法を変えない。分岐は`os.name`で行い、Windows側の式は従来のまま残す。
- Linux専用の環境は`.venv-linux`、保存先は`~/.local/share`とする。Windowsの`.venv`や`%LOCALAPPDATA%`と共有しない。
- Linuxで変えた点は`LINUX.md`と`tools/install-linux.sh`に反映する。Windows向けの説明書にはLinux固有の手順を混ぜない。
