# Monitor Brightness Sync

ノートPC内蔵画面の明るさに外部モニターを追従させるWindows用常駐ツールです。約3秒ごとに確認し、通常はウィンドウを出しません。

## ビルド

Windows付属の.NET Framework 4.xのC#コンパイラを使います。追加パッケージは不要です。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

出力は `bin\MonitorBrightnessSync.exe` です。起動する前に個人設定を作成してください。設定がない・不正な場合は画面を変更せず終了し、ログを残します。内蔵画面のWMI明るさ情報が取得できるPCが対象です。

## 個人設定

`config.example.xml`を参考に `%LOCALAPPDATA%\MonitorBrightnessSync\config.xml` を作成します。設定・ログ・実行ファイルは公開しません。設定例のIDは架空です。実際のIDは次の読み取り専用コマンドで確認できます。

```powershell
Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorID | Select-Object InstanceName
```

`DISPLAY\...\...` の最初の `...` が設定の `id` です。登録したIDに一致する画面だけを変更します。

| 属性 | 意味 |
| --- | --- |
| `id` | モニターの機種ID。必須、重複不可。同型の複数画面には同じ設定が適用される |
| `name` | ローカルログ用の名前 |
| `hardware` | `true`ならDDC/CIでバックライトを追従させる。既定はfalse |
| `scale` | 内蔵画面の設定値に掛ける倍率。0〜1、既定は1 |
| `gamma` | 固定のソフトウェア補正。50〜100、省略すると変更しない |
| `softwareFollow` | `true`なら内蔵画面の値にソフトウェア補正で追従。`gamma`と併用不可 |
| `minimum` | ソフトウェア追従の下限。5〜100、既定は5 |
| `blueGain` | 対応機種の青ゲイン（VCP 0x1A）。0〜100、省略すると変更しない |

DDC/CIはモニター側でも有効にする必要があります。APIが非対応なら変更できない場合があります。値をそろえても画面間の実際の輝度が一致するわけではなく、測光器による調整ではありません。

50%未満のソフトウェア追従には、クリックを透過しフォーカスを奪わない黒いレイヤーを使います。省電力のためのバックライト調整とは異なります。HDR・全画面アプリ・セキュアデスクトップなどでは期待どおり表示されない場合があります。ソフトを終了するとレイヤーは消えますが、ハードウェア明るさとガンマは自動復元しません。

設定変更は実行中の本ツールを閉じてから再起動すると反映されます。既存の自動起動がある場合は実行ファイルのパスを維持するか、登録先も更新してください。二重起動は同一ログオンセッションのミューテックスで防ぎます。別の明るさ調整ソフトとの併用は競合する場合があります。

ログは `%LOCALAPPDATA%\MonitorBrightnessSync\sync.log` に保存し、約1MBを超えると古い内容を破棄します。通信・クラウド同期は行いません。

## 検証

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\verify.ps1
```

ビルドと設定例・範囲外値・競合・重複・誤記の拒否を確認します。検証は同期ループを起動せず、画面の明るさを変更しません。
