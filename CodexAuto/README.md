# Codex Auto

標準のCodex CLI画面を使いながら、新しいターンを送信するたびにOllayaがモデルと推論設定を選ぶツールです。会話を作り直すのではなく、同じ会話のまま切り替えます。

## 導入

Windows、Python 3.11以降、uv、サインイン済みのCodex CLI、Ollayaが必要です。`laya:multilingual`をあらかじめOllayaへ導入してください。

このフォルダで:

```powershell
uv tool install --editable . --python 3.11
```

uvの実行ファイル用フォルダがPATHにある状態で使えます。必要なら`uv tool update-shell`を実行して新しいターミナルを開いてください。WebSocket中継用の`websockets`をuvの専用環境へ導入します。Codex CLI 0.162.1で動作確認しています。

## 使い方

```powershell
codex-auto "家事のチェックボックスが保存されない原因を調べて直して"
codex-auto
codex-auto --dry-run "英語の一文を日本語に翻訳して"
codex-auto --list-models
codex-auto --model gpt-6.1-sol --effort high "この処理を見直して"
codex-auto "この不具合を調べて" -- --sandbox read-only --no-alt-screen
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Get-Content -Raw -Encoding UTF8 request.txt | codex-auto -
```

引数を省略すると標準CLIの入力欄が開きます。`--dry-run`は単独の依頼に対する選択結果の確認だけです。`--dry-run --json`で依頼文を含まない選択結果JSONを出力できます。通常のCodexオプションは`--`の後ろへ渡します。手動のモデル・推論指定は優先し、矛盾した重複指定はエラーにします。

`--model`はモデルを固定し、`--effort`は推論設定を固定します。CLIのモデル変更が`thread/settings/update`として通知された場合も、変更したモデル・推論設定を以降のターンで優先します。自動選択へ戻す場合は、固定指定なしで`codex-auto`を起動し直してください。

## 選択方針

共有daemonは使用しません。開いている間だけ専用のローカルApp Serverを動かし、標準CLIがそこへ直接接続します。Codexでは`--remote`と`--no-daemon`を同時指定できないため、明示した`--no-daemon`はこの専用接続に置き換えます。通常の`codex`コマンドや設定ファイルは変更しません。

今回の依頼に加え、直近のユーザー入力・最終応答を最大4件、それぞれ約300文字の抜粋として判断に使います。再開した会話では、サーバーから受け取った直近の履歴を使います。コード全体・ファイル・ツール出力をOllayaへ渡すことはありません。長い背景や画像を判断しきれない場合は高性能側を選びます。

| 判定 | 優先モデル | 推論 |
| --- | --- | --- |
| 翻訳・形式変換などの定型作業 | GPT-6 Luna | low |
| コード修正・機能追加 | GPT-6.1 Sol | medium |
| 原因調査・設計・研究、重要データ等への影響、曖昧な判定 | GPT-6.1 Sol | high |

`gpt-6-astra`（Astra）と`gpt-6-sol`（6 Sol）は使用禁止です。自動選択・代替候補・モデル一覧から除外し、起動時やCLIのモデル変更での手動指定も拒否します。`gpt-6.1-sol`（6.1 Sol）は使用できます。

Codexの設定で指定されたモデル一覧、またはローカルの`models_cache.json`に存在するモデルと推論設定を選びます。一覧は利用権限の保証ではなく、実際の実行時にはCodexの認証・プランによる制約が適用されます。上位モデルが一覧にない場合はSol/highなどの候補を使います。自動選択ではultraを選びません。

作業種類の確信度が0.65未満、重要な影響の判定値が0.35以上、入力の切り詰め、通信・判定失敗の場合は高性能側を選びます。これらは初期の経験的な閾値であり、適切なモデル選択を保証するものではありません。短い曖昧な依頼や複雑な背景がある場合は、依頼へ具体的な条件を含めるか手動指定してください。

Codex Meterに新鮮な残量がある場合は起動前に表示します。残量だけを理由に難しい作業のモデルを下げません。

切り替えるのは`turn/start`の直前です。実行中の割り込み入力（`turn/steer`）はモデル変更を受け付けないため、その場では変更せず、次の新しいターンで反映します。モデル自身のツール呼び出しごとに切り替える機能ではありません。送信時にはOllayaの判断を待つため、通常のCLIより待ち時間が加わります。

## 実行・データ

- Ollayaへの接続はlocalhost/127.0.0.1/::1に限定し、プロキシとHTTPリダイレクトを使いません。
- Ollayaが停止していれば見えないバックグラウンドで起動します。新しく起動するサーバーは環境変数の指定がなければCPU・2スレッドを使います。既存サーバーの設定はそのままです。
- インストール済みの判断モデルだけを使います。自動ダウンロードや更新はありません。判断モデルはリクエスト後60秒でアンロードする設定です。
- 判断用の抜粋はメモリ内だけで保持し、最大16会話までとします。依頼文、判定結果、認証情報をファイルに保存しません。起動したCodex自身は通常どおり会話を記録します。
- Codexの設定は読み取りだけで、モデル・推論設定は各ターンの要求で渡します。会話ID、承認・サンドボックス設定、モードの指示、添付は維持します。
- 中継は`127.0.0.1`の空きポートだけで待ち受け、起動ごとのランダムな認証トークンを使用します。トークンはメモリと子プロセスの環境変数だけで保持し、コマンド引数やファイルには保存しません。ブラウザからの接続と追加クライアントは拒否します。
- 専用App Serverとは標準入出力で通信し、App Server自身のネットワーク待ち受けは作りません。CLI終了時は中継と専用サーバーを終了します。他のCodexプロセスは終了しません。
- Codexは今のターミナルと作業フォルダを引き継ぎます。依頼文は引数の配列として渡し、シェルのコードとして評価しません。
- npm版は標準のNodeランチャーを経由し、インストール情報と終了処理を引き継ぎます。
- OpenAI以外のプロバイダー/OSSを使う場合はモデルを手動指定してください。
- 外部サーバーを指定する`--remote`は併用できません。中継には実験的なApp Server APIを使用しているため、Codexの更新で互換性が変わる可能性があります。
- `CODEX_AUTO_OLLAYA_MODEL`で判断モデル、`OLLAYA_HOST`でローカル接続先、`OLLAYA_API_KEY`でOllayaの認証、`CODEX_AUTO_CODEX_EXE`でCodex実行ファイルを指定できます。

確認:

```powershell
uv run --locked python -m unittest discover -s tests
uv run --locked python tools/verify_protocol.py
```

`verify_protocol.py`は一時的な空のCodex設定で接続・モデル一覧・一時会話の作成を確認します。認証情報やモデル推論は使いません。実際のモデル切り替えまで確認する場合は、利用枠を消費することを了解したうえで`uv run --locked python tools/verify_live_turns.py --allow-inference`を実行してください。二つの短い応答だけを、MCP無効・読み取り専用の一時会話で確認します。

資料: [Ollaya API](https://ollaya.dev/docs/api)、[Codex CLI](https://learn.chatgpt.com/docs/cli/reference)、[Codex App Server](https://learn.chatgpt.com/docs/app-server)
