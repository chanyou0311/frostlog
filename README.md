# frostlog

車載のポータブル冷蔵庫（Anker Solix EverFrost 2）が Bluetooth で流す状態と、その周りの温湿度を
Raspberry Pi で記録し、バッテリー残量にまつわる問いに Slack で答えるためのリポジトリ。

各ユニットが自分のディレクトリと uv プロジェクトを持ち、境界はデータコントラクト
（`contracts/*.odcs.yaml`）が持つ。データを出すのは次の 4 つ。

| | 何を出すか | どこに |
|---|---|---|
| `frostlog-collection` | 測ったものをそのまま | [`collection/`](collection/) → GCS |
| `frostlog-semantics` | 意味づけしたテーブル | [`semantics/`](semantics/) → BigQuery |
| `frostlog-notifier` | Slack の通知 | [`notifier/`](notifier/) → Cloud Run |
| `frostlog-contracts` | 契約テストの結果 | [`contracts/job/`](contracts/job/) → Cloud Run job |

冷蔵庫は同時に 1 つの機器としか話せないので、Bluetooth 接続は
[`gateway/`](gateway/) の常駐サービスだけが持ち、`collection/` はそれを購読して記録する。
ゲートウェイはデータを出さない。

各ユニットの使い方はそれぞれの `README.md` と `Makefile` に、リポジトリ全体の決まりごとと
踏みやすい罠は [AGENTS.md](AGENTS.md) にある。

## ライセンス

[MIT](LICENSE)。Anker 機器の BLE プロトコル（フレーム形式、鍵交換、クライアント側の固定鍵）は
[flip-dots/SolixBLE](https://github.com/flip-dots/SolixBLE)（MIT）の解析を参照し、そのキャプチャを
テストベクタとして使っている。
