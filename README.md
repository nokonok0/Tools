# Tools

Windowsで日常的に使う小さなツールをまとめたリポジトリです。

| ツール | 用途 | 起動・導入 |
| --- | --- | --- |
| [FloatingEditKeys](FloatingEditKeys/) | マウスの右長押しで呼び出す編集キーウィジェット | [操作・導入方法](FloatingEditKeys/README.md) |
| [Codex Meter](CodexMeter/) | Codexの残量と、モデル・推論設定別の上限までの推定時間を表示するトレイアプリ | [起動・計算方法](CodexMeter/README.md) |

各ツールは個別に起動できます。FloatingEditKeysにはAutoHotkey v2、Codex MeterにはPython 3.11以降が必要です。

## Codex Meterの起動

```powershell
cd CodexMeter
python -m pip install -r requirements.txt
```

`CodexMeter/CodexMeter.pyw`をダブルクリックすると、時計横のトレイに常駐します。アイコンをクリックすると小さなパネルが開き、別の場所をクリックすると閉じます。右クリックから更新・終了できます。

## 個人データ

公開対象はコード・説明書・テストです。Codexの会話、認証情報、使用量履歴、通知履歴を含めません。

Codex Meterの観測値は`%LOCALAPPDATA%\CodexMeter`に保存し、元のCodexログは読み取りだけ行います。観測値には会話本文や認証情報を保存しません。

## 開発

Codex Meterの変更確認:

```powershell
cd CodexMeter
python -m unittest discover -s tests
```

機能・使い方の変更は各ツールのREADMEと[REQUIREMENTS.md](REQUIREMENTS.md)に反映してください。
