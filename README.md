# frostlog

ポータブル冷蔵庫（Anker EverFrost 2）の電力と、その周囲の温湿度を Raspberry Pi で記録するデータロガー。

- `frostlog read ambient` — I2C の温湿度センサー（DHT20 / AM2320）を定期的に読む
- `frostlog read cooler` — 冷蔵庫が Bluetooth で送ってくるメッセージを、意味づけせずにそのまま残す
- `frostlog upload DIR` — 溜めた JSONL を S3 互換のバケット（Cloudflare R2）へ送る。再実行しても同じ結果になる
- `frostlog decode` — 冷蔵庫レコードの生データを分解して眺める（開発用）

レコードは 1 行 1 JSON。`ts`（UTC）、`uptime`（起動からの秒）、`boot_id`、`type` を全行が持ち、`--output DIR` で `DIR/<type>/<日付>.jsonl` に保存する。集計は Mac の DuckDB が R2 のファイルを直接読む（`analysis/`）。

## 使い方

```sh
uv sync
uv run frostlog read ambient --interval 2 --count 3      # 配線の確認
uv run frostlog read cooler --scan                       # 近くの Bluetooth 機器
uv run frostlog read cooler --duration 60 | uv run frostlog decode
```

設定は環境変数 `FROSTLOG_*`（`scripts/env.example`）。

Pi には Mac から `scripts/deploy.sh` で配る。Pi 上ではユーザー単位の systemd が
`frostlog-ambient` を常駐させ、`frostlog-upload.timer` が 30 分おきに送る。`frostlog-cooler` は
`FROSTLOG_COOLER_ADDRESS` を設定すると常駐する（未設定だと近くの Anker 機器を何でも掴んでしまうため）。
データは `~/.local/state/frostlog`、設定は `~/.config/frostlog/env`、ログは
`journalctl --user-unit frostlog-ambient` で見る。
