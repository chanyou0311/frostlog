# frostlog

ポータブル冷蔵庫（Anker EverFrost 2）が Bluetooth で送ってくるメッセージと、その周囲の温湿度を
Raspberry Pi で記録し、クラウドのバケットへ送るデータロガー。スキーマの正は
`contracts/collection.odcs.yaml`（収集データプロダクトのデータ契約）。

- `frostlog read cooler` — 冷蔵庫のメッセージを受け取り、生のバイト列・復号した本文・意味づけした値、
  そのときの車内の温湿度（DHT20）を 1 行に記録する
- `frostlog upload DIR` — 溜めた JSONL を S3 互換 API 経由で GCS へ送る。再実行しても同じ結果になる
- `frostlog read ambient` — 温湿度センサーだけを読む（配線確認用）
- `frostlog decode` — 未知のメッセージをパラメータに分解して眺める（開発用）

レコードは 1 行 1 JSON。`ts`（UTC）、`uptime_seconds`（起動からの秒）、`boot_id`、`ts_synced`、`type` を
全行が持ち、`--output DIR` で `DIR/<stream>/<日付>.jsonl` に保存する。アップロードは
`v1/<stream>/dt=<日付>/<開始オフセット>.jsonl.gz` という不変のチャンクで、どこまで送ったかはバケット側の
メタデータ（`end`）が正。空行や NUL で埋まった行はチャンクに入れない（オフセットはローカルのバイト位置のまま）。
アップロード自体の記録（`upload_started` / `upload_done`）は `events` 以外のチャンクを送った回だけ残す
（毎回書くと events だけが増え続け、5 分ごとに LIST + PUT を払うことになるため）。

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
全行を書き換えるのでバイトオフセットがすべてずれる。そのディレクトリに対して v1 の `frostlog upload` を
一度でも動かした後には実行できない（バケット上のチャンク名が指す行が変わってしまう）。スクリプトは
同じ理由で `<DIR>/.upload-cache.json` を削除し、最後に処理件数の 1 行を出す。
