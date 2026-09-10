# frostlog

ポータブル冷蔵庫（Anker EverFrost 2）が Bluetooth で送ってくるメッセージと、その周囲の温湿度を
Raspberry Pi で記録し、クラウドのバケットへ送るデータロガー。スキーマの正は
`contracts/raw.odcs.yaml`（収集データプロダクトのデータ契約）。

- `frostlog read cooler` — 冷蔵庫のメッセージを受け取り、生のバイト列・復号した本文・意味づけした値、
  そのときの車内の温湿度（DHT20）を 1 行に記録する
- `frostlog upload DIR` — 溜めた JSONL を S3 互換 API 経由で GCS へ送る。再実行しても同じ結果になる
- `frostlog read ambient` — 温湿度センサーだけを読む（配線確認用）
- `frostlog decode` — 未知のメッセージをパラメータに分解して眺める（開発用）

レコードは 1 行 1 JSON。`ts`（UTC）、`uptime_seconds`（起動からの秒）、`boot_id`、`ts_synced`、`type` を
全行が持ち、`--output DIR` で `DIR/<stream>/<日付>.jsonl` に保存する。アップロードは
`v1/<stream>/dt=<日付>/<開始オフセット>.jsonl.gz` という不変のチャンクで、どこまで送ったかはバケット側の
メタデータ（`end`）が正。

## 使い方

```sh
uv sync
uv run frostlog read ambient --interval 2 --count 3      # 配線の確認（--sensor am2320 も可）
uv run frostlog read cooler --scan                       # 近くの Bluetooth 機器
uv run frostlog read cooler --duration 60 | uv run frostlog decode
uv run python scripts/build_samples.py                   # contracts/samples を作り直す
```

設定は環境変数 `FROSTLOG_*`（`scripts/env.example`）。

Pi には Mac から `scripts/deploy.sh` で配る。Pi 上ではユーザー単位の systemd が `frostlog-cooler` を
常駐させ、`frostlog-upload.timer` が 5 分おきに送る。`frostlog-cooler` は
`FROSTLOG_COOLER_ADDRESS` を設定して再起動すると常駐する（未設定だと近くの Anker 機器を何でも
掴んでしまうため、ユニットの `ExecCondition` で起動をスキップする）。
データは `~/.local/state/frostlog`、設定は `~/.config/frostlog/env`、ログは
`journalctl --user-unit frostlog-cooler` で見る。
`FROSTLOG_HEALTHCHECK_URL` を設定すると、失敗なく送れた回ごとに healthchecks.io などへ ping する
（数日届かなければ通知する見張り）。

v1 より前に記録したファイルは `scripts/migrate_raw_v1.py` で契約どおりの形に書き換える（Pi 上で 1 回だけ）。

## semantic/

バケットに届いた JSONL を BigQuery に取り込み、dbt でディメンショナルモデルに変換する
Cloud Run サービス（`frostlog-semantic`）。`semantic/service` が Python、`semantic/dbt` が
dbt プロジェクト、スキーマの取り決めは `contracts/semantic.odcs.yaml`。

```sh
cd semantic
make install        # service の uv プロジェクト（dbt と datacontract-cli を含む）
make check          # ruff / ty / pytest / dbt parse — BigQuery なしで通る
make build          # 全期間を作り直す（BigQuery が要る）
make unit-test      # dbt のユニットテスト（同上）
make ci-warehouse   # contracts/samples から CI データセットを作り直して契約を検査する
```

設定は環境変数（Terraform が Cloud Run に与える）。`FROSTLOG_RAW_BUCKET`（これ以外の
バケットのイベントは無視する）、`FROSTLOG_BQ_DATASET`、`FROSTLOG_BQ_DATASET_CI`、
`FROSTLOG_SEMANTIC_UPDATED_TOPIC`（トピックの短い名前）、`FROSTLOG_GCP_PROJECT`（省略時は
Application Default Credentials のプロジェクト）。healthchecks.io の URL とバケットの HMAC 鍵は
Secret Manager にあり、`FROSTLOG_CONTRACT_TEST_HEALTHCHECK_URL_SECRET` と
`FROSTLOG_RAW_HMAC_SECRET` にはその名前だけを渡す（読めなければ ping と raw 契約の検査を飛ばす）。
