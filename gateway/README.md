# frostlog-gateway

冷蔵庫（Anker Solix EverFrost 2）と話す唯一のプロセス。Raspberry Pi に常駐し、BLE の接続と暗号と
EverFrost のプロトコルを持って、**受け取ったものをイベントストリームに流し、許可した設定だけを
書き込む**。

冷蔵庫は同時に 1 つの central しか受け付けず（接続中は広告を止める）、セッション鍵は接続のたびに
変わる。読む人と書く人がそれぞれ接続しにいくことはできないので、接続を持つ役を 1 つに切り出した。
記録するかどうかは `../collection`、書き込むかどうかは `../controller` の判断で、ここはどちらでもない。

守っていること:

- **ディスクに書かない。** 状態ファイルもレコードも持たない。落ちたら次の接続からやり直すだけ
- **時刻はここで刻む。** BLE の通知を受け取った瞬間の `ts` / `uptime_seconds` / `boot_id` を付ける
  （読み手が受け取った時点で刻むと、キューの遅れぶん自然キーと周辺温湿度の時刻がずれる）
- **欠けは防がず、見せる。** クライアントごとのキューが溢れたら古いものを捨て、`dropped` を流す。
  プロセス全体で通し番号を振るので、欠けはクライアントから番号の飛びとして見える
- **セッションの秘密鍵は流さない。** 交渉の完了イベントに `secret` は載せない
- **書けるのは許可リストにある設定だけ。** 範囲も含めてここ 1 か所が持つ。同時に処理する命令は 1 つ
- **データ契約を知らない。** 契約を知るのは collection だけで、ここが流すのはゲートウェイ独自の形

冷蔵庫は接続直後の状態通知（`4402`）を送ってこないことがあるので、交渉が終わった時点で状態を
問い合わせ、答えが来るまでは間を置いて問い合わせ直す。表示単位が °F のときは書き込む値も °F
として解釈されるため、最新の状態が分かるまで設定温度の書き込みは受け付けない。

## 使い方

```sh
cd gateway
make install                                   # uv プロジェクト
make check                                     # ruff / ty / pytest（Pi も冷蔵庫も要らない）
uv run frostlog-gateway scan                   # 近くの Bluetooth 機器（冷蔵庫のアドレスを探す）
uv run frostlog-gateway run --address AA:BB:…  # ソケットを開いて常駐する
socat - UNIX-CONNECT:"$XDG_RUNTIME_DIR"/frostlog/events.sock | uv run frostlog-gateway decode
```

`decode` は未知のメッセージをパラメータに分解して眺める開発用の道具で、ストリームの
`message` イベントでも collection のレコードでも読める。

Pi には Mac から `make deploy`（`PI=user@host` で宛先を変える）で配る。ユーザー単位の systemd が
常駐させ、ログは `journalctl --user-unit frostlog-gateway` で見る。冷蔵庫のアドレスを設定して
再起動すると常駐する（未設定だと近くの Anker 機器を何でも掴んでしまうため、ユニットの
`ExecCondition` で起動をスキップする）。設定は環境変数 `FROSTLOG_*`（`scripts/env.example`）。
