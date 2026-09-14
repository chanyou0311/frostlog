# notifier/

`frostlog-semantics` のテーブルを読んで Slack に要約を投稿するデータアプリケーション
（Cloud Run service `frostlog-notifier`）。読むのは `contracts/semantics.odcs.yaml` の
テーブルと `contracts/signals.odcs.yaml` のイベントだけで、ウェアハウスには何も書かない。
何も覚えない: どの要約も、ジョブが起きた時点から遡る窓の絵で、差分ではない。

```sh
cd notifier
make install        # uv プロジェクト
make check          # ruff / ty / pytest — BigQuery も Slack も要らない
uv run uvicorn frostlog_notifier.app:app --port 8080   # 手元で起こす (ADC が要る)
```

エンドポイントは `src/frostlog_notifier/app.py` にある。Cloud Scheduler が定時に叩く要約と、
Pub/Sub が push してくる signals の 2 種類で、後者から作るのは契約テスト失敗の警告だけ。

設定は環境変数（Terraform が Cloud Run に与える）。`FROSTLOG_BQ_DATASET`、
`FROSTLOG_SLACK_BOT_TOKEN_SECRET`（Secret Manager の名前。トークンそのものは渡さない）、
`FROSTLOG_SLACK_CHANNEL`、`FROSTLOG_GCP_PROJECT`（省略時は Application Default Credentials の
プロジェクト）。トークンが読めないあいだは dry run になり、送るはずだった本文をログに出す。
手元で本物に送るときだけ `FROSTLOG_SLACK_BOT_TOKEN` に値を入れる。
