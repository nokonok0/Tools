# Codex Auto

Ollayaで依頼の種類と影響を判定し、モデルと推論設定を選んで通常のCodex CLIを起動するツールです。選択するのは起動時の一度だけで、対話中の自動切り替えは行いません。

## 導入

Windows、Python 3.11以降、uv、サインイン済みのCodex CLI、Ollayaが必要です。`laya:multilingual`をあらかじめOllayaへ導入してください。

このフォルダで:

```powershell
uv tool install --editable . --python 3.11
```

uvの実行ファイル用フォルダがPATHにある状態で使えます。必要なら`uv tool update-shell`を実行して新しいターミナルを開いてください。依存パッケージはなく、uvの専用環境から実行します。

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

引数を省略すると最初の依頼を入力できます。`--dry-run`は選択結果の確認だけです。`--dry-run --json`で依頼文を含まない選択結果JSONを出力できます。通常のCodexオプションは`--`の後ろへ渡します。手動のモデル・推論指定は優先し、矛盾した重複指定はエラーにします。

## 選択方針

| 判定 | 優先モデル | 推論 |
| --- | --- | --- |
| 翻訳・形式変換などの定型作業 | GPT-6 Luna | low |
| コード修正・機能追加 | GPT-6.1 Sol | medium |
| 原因調査・設計・研究、重要データ等への影響、曖昧な判定 | GPT-6 Astra | high |

Codexの設定で指定されたモデル一覧、またはローカルの`models_cache.json`に存在するモデルと推論設定を選びます。一覧は利用権限の保証ではなく、実際の実行時にはCodexの認証・プランによる制約が適用されます。上位モデルが一覧にない場合はSol/highなどの候補を使います。自動選択ではultraを選びません。

作業種類の確信度が0.65未満、重要な影響の判定値が0.35以上、入力の切り詰め、通信・判定失敗の場合は高性能側を選びます。これらは初期の経験的な閾値であり、適切なモデル選択を保証するものではありません。短い曖昧な依頼や複雑な背景がある場合は、依頼へ具体的な条件を含めるか手動指定してください。

Codex Meterに新鮮な残量がある場合は起動前に表示します。残量だけを理由に難しい作業のモデルを下げません。

## 実行・データ

- Ollayaへの接続はlocalhost/127.0.0.1/::1に限定し、プロキシとHTTPリダイレクトを使いません。
- Ollayaが停止していれば見えないバックグラウンドで起動します。新しく起動するサーバーは環境変数の指定がなければCPU・2スレッドを使います。既存サーバーの設定はそのままです。
- インストール済みの判断モデルだけを使います。自動ダウンロードや更新はありません。判断モデルはリクエスト後60秒でアンロードする設定です。
- 依頼文、判定結果、認証情報をファイルに保存しません。起動したCodex自身は通常どおり会話を記録します。
- Codexの設定は読み取りだけで、モデル・推論設定は起動引数で渡します。承認・サンドボックス設定の自動変更はありません。
- Codexは今のターミナルと作業フォルダを引き継ぎます。依頼文は引数の配列として渡し、シェルのコードとして評価しません。
- npm版は標準のNodeランチャーを経由し、インストール情報と終了処理を引き継ぎます。
- OpenAI以外のプロバイダー/OSSを使う場合はモデルを手動指定してください。
- `CODEX_AUTO_OLLAYA_MODEL`で判断モデル、`OLLAYA_HOST`でローカル接続先、`OLLAYA_API_KEY`でOllayaの認証、`CODEX_AUTO_CODEX_EXE`でCodex実行ファイルを指定できます。

確認:

```powershell
uv run --locked python -m unittest discover -s tests
```

資料: [Ollaya API](https://ollaya.dev/docs/api)、[Codex CLI](https://learn.chatgpt.com/docs/cli/reference)
