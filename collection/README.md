# frostlog-collection

ポータブル冷蔵庫（Anker EverFrost 2）が流すメッセージと、その周囲の温湿度を Raspberry Pi で
記録し、クラウドのバケットへ送るデータロガー。スキーマの正は `../contracts/collection.odcs.yaml`
（収集データプロダクトのデータ契約）。

冷蔵庫の Bluetooth 接続は持たない。冷蔵庫は同時に 1 つの central としか話せないので、接続は
`frostlog-gateway` が 1 つだけ持ち、ここはそのイベントストリームを読む側にいる。

- `frostlog read cooler` — ゲートウェイのストリームを受け、生のバイト列・復号した本文・
  意味づけした値と、そのときの周辺の温湿度（DHT20）を 1 行に記録する
- `frostlog upload DIR` — 溜めた JSONL を S3 互換 API 経由で GCS へ送る。再実行しても同じ結果になる
- `frostlog read ambient` — 温湿度センサーだけを読む（配線確認用）

レコードの `ts` / `uptime_seconds` / `boot_id` / `ts_synced` は、受け取った時刻ではなく
**ゲートウェイが BLE 通知を受けた時刻**をそのまま持つ（この 4 つが行の自然キーであり、読み手側で
刻むとキューの遅れぶん全メッセージがずれる）。周辺の温湿度だけはここで測る — センサーはこちら側に
あり、その値は「そのメッセージのときの空気」だから。ストリームの取りこぼし（ゲートウェイ側の
キュー溢れ、ゲートウェイの停止）は隠さず行として残す。

レコードは 1 行 1 JSON。`--output DIR` で `DIR/<stream>/<日付>.jsonl` に保存する。アップロードは
`v1/<stream>/dt=<日付>/<開始オフセット>.jsonl.gz` という不変のチャンクで、どこまで送ったかはバケット側の
メタデータ（`end`）が正。空行や NUL で埋まった行はチャンクに入れない（オフセットはローカルのバイト位置のまま）。
アップロード自体の記録（`upload_started` / `upload_done`）は `events` 以外のチャンクを送った回だけ残す
（毎回書くと events だけが増え続け、送るたびに LIST + PUT を払うことになるため）。

## 使い方

```sh
cd collection
make install                                             # uv プロジェクト
make check                                               # ruff / ty / pytest
uv run frostlog read ambient --interval 2 --count 3      # 配線の確認（--sensor am2320 も可）
uv run frostlog read cooler --duration 60                # ゲートウェイが動いている必要がある
make samples                                             # ../contracts/samples を作り直す
```

未知のメッセージを眺めるときは、記録した行をそのまま `frostlog-gateway decode` に流す
（プロトコルを知っているのはゲートウェイの側）。

設定は環境変数 `FROSTLOG_*`（`scripts/env.example`）。ゲートウェイと同じ
`~/.config/frostlog/env` を読む。

Pi には Mac から `make deploy`（`PI=user@host` で宛先を変える）で配る。このディレクトリの中身が
そのまま Pi の `~/frostlog/` になるので、systemd unit が指す `~/frostlog/.venv/bin/frostlog` と
インストーラの位置関係は Pi 側で変わらない。Pi 上ではユーザー単位の systemd が `frostlog-cooler` を
常駐させ、`frostlog-upload.timer` が定期的に送る。ゲートウェイが止まっていても `frostlog-cooler` は
落ちず、復帰を待つ（待っていたこと自体も記録に残る）。
データは `~/.local/state/frostlog`、設定は `~/.config/frostlog/env`、ログは
`journalctl --user-unit frostlog-cooler` で見る。
`FROSTLOG_HEALTHCHECK_URL` を設定すると、失敗なく送れた回ごとに healthchecks.io などへ ping する
（数日届かなければ通知する見張り）。
