# AGENTS.md

車載のポータブル冷蔵庫 (Anker Solix EverFrost 2) が BLE で流す状態と、その周りの温湿度を
Raspberry Pi で記録し、バッテリー残量にまつわる問いに Slack で答えるためのリポジトリ。

**2 つのデータプロダクトと 1 つのデータアプリケーション**で構成し、境界はデータコントラクト
(`contracts/*.odcs.yaml`) が持つ。契約が守られているかを定時に訊くのは、どちらのプロダクトにも
属さない 4 つめのユニット。

| | 何を出すか | 実体 |
|---|---|---|
| `frostlog-collection` | 測ったものをそのまま | Pi の収集/送出 (`collection/`) → GCS |
| `frostlog-semantics` | 意味づけしたテーブル | dbt (`semantics/dbt/`) → BigQuery |
| `frostlog-notifier` | Slack の通知 (データアプリケーション) | `notifier/` → Cloud Run |
| `frostlog-contracts` | 契約テストの結果 | `contracts/job/` → Cloud Run job |

Pi にはデータを出さないユニットがもう 2 つある。冷蔵庫は同時に 1 つの central としか話せず、
鍵も接続ごとに変わるので、**接続を持つプロセスを 1 つに切り出した**。

| | 役割 | 実体 |
|---|---|---|
| `frostlog-gateway` | 冷蔵庫と話す唯一のプロセス。聞いたものを流し、許可した書き込みを受ける | Pi の常駐サービス (`gateway/`) |
| `frostlog-controller` | 在宅が変わったときに設定温度のコマンドを出す | Pi の systemd timer ジョブ (`controller/`) |

答えたい問いは 4 つ: A 残量が条件でどう変わるか / B 帰宅時の残量と翌朝の見通し /
C 外出中あと何時間もつか / D 設定温度に到達するまでの時間。

## データの流れ

何がいつ動くか (間隔、時刻、リソース名) はここに書かない。`fumo-terraform` の `.tf`、Pi の
systemd unit (各ユニットの `scripts/systemd/`)、各 `Makefile` がそのまま語る。ここにあるのは形だけ。

```
Pi: frostlog-gateway   (BLE 接続を保持する唯一のプロセス。聞いた瞬間に時刻を刻んでイベントを
  │  ▲                    流し、許可したコマンドだけを冷蔵庫に書く。ディスクには書かない)
  │  └─ frostlog-controller  (在宅が変わったら設定温度のコマンドを 1 回だけ出す)
  ▼
frostlog-collection    (ストリームを購読して記録。周辺温湿度はこちらで測る。timer が前回の
  │                      続きからバイト単位で差分を送出)
  ▼
GCS  v1/{stream}/dt=YYYY-MM-DD/{offset:012d}.jsonl.gz   ← オブジェクト名は「ローカルファイルの何バイト目から」
  │ BigQuery Data Transfer Service (バケットを自前で読む。リポジトリにコードは無い)
  ▼
BigQuery raw   ← バケットの形そのもの。列を足さない
  │ Cloud Scheduler が定時に frostlog-semantics を起こす。積まれた行数 (tables.get の num_rows)
  │ がバケットに置いた目印より増えていたときだけ組む。増えていなければ何もしない
  ▼   dbt build 一式 → Pub/Sub frostlog-signals
Cloud Run service frostlog-notifier → Slack
      signals から作るのは契約テスト失敗の警告だけ。サマリは Cloud Scheduler が定時に起こす

Cloud Scheduler が定時に Cloud Run job frostlog-contracts を起こし、契約を本番データに当てる → signals
```

## 必須コマンド

```bash
make -C collection check                         # コレクター (Pi のパッケージ)。lint + test
make -C gateway check                            # 冷蔵庫と話すサービス。lint + test
make -C controller check                         # 在宅で設定温度を切り替えるジョブ。lint + test
make -C semantics check                          # lint + test + dbt parse (ウェアハウス不要)
make -C semantics ci-warehouse                   # 実 BigQuery で通し。ADC が要る
make -C contracts check                          # 契約テストジョブ自身の lint + test
make -C contracts lint                           # 契約そのものが well-formed か
make -C contracts test CONTRACT=collection       # 本番のデータを契約に当てる。ADC が要る
make -C notifier check                           # Slack アプリケーションの lint + test
make -C collection deploy                        # Pi へ配布 (PI=user@host で宛先を変える)
make -C gateway deploy                           # 同上。ユニットごとに別のディレクトリへ入る
make -C controller deploy                        # 同上
```

## 踏みやすい罠 (MUST)

ここに書いてあるものは**全て実際に踏んで直したもの**。次に触る人が同じ穴に落ちないように残す。

- **BigQuery の課金はクエリ本数で決まる。** 「1 クエリあたり 10 MiB、かつ参照テーブルあたり
  10 MiB」が下限。実測で 1 ビルド 550 MiB 課金 / 29 MiB 実処理 = **9 割が下限の水増し**。
  したがって**パーティション調整・列の絞り込み・クラスタリングは無意味**。効くのは実行頻度と
  クエリ本数だけ。無料枠は 1 TiB/月 (請求アカウント単位)、1 日あたり約 34 GiB。
- **dbt の `config()` はパース時に解決される。** `execute` が False のその時点で、`run_query`
  の結果は空。`incremental_predicates` にコンパイル中のクエリ結果を入れると `in (null)` が
  焼き付き、MERGE が何にも一致せず**毎回全行が INSERT される**。本番で 55,762 行 / 実体
  23,861 キーまで膨らんだ。実行依存の値を parse 解決される config に入れてはいけない。この後、
  全テーブルを毎回作り直す設計 (`+materialized: table`) にして MERGE そのものを無くした。
- **ロードジョブは、自分のスキーマが名前を挙げた列に DEFAULT を入れない。** NULL を入れる。
  Data Transfer Service は常に宛先の全列を名前で挙げるので、`DEFAULT CURRENT_TIMESTAMP()` は
  **DTS 経由では絶対に発火しない**。`load_table_from_uri` で手元から試すと動くので気づけない
  (本番 31,966 行が全て NULL になった)。取り込み時刻が要るなら、列ではなく**テーブルの
  メタデータに訊く** — `tables.get` は無料で、`num_rows` は行が実際に積まれたときだけ動く
  (`modified` は 0 行のロードでも動いてしまうので使えない)。
- **CI がサンプルを一括で配信すると、実運用のチャンク境界を踏めない。** チャンクは時間で
  切れるので、境界の直前の報告は**その続きを別のチャンクから受け取る**。`ci-warehouse` は
  サンプルを 2 つに割って配信し、両方が届いてから 1 回だけ組んで `held_seconds` を契約に
  当てる (`semantics/Makefile`)。配信の途中で組んでも、全テーブルを作り直す以上は何も増えない。
- **botocore 1.36 以降は PUT に CRC32 を付ける。** GCS の S3 互換 API はこれを受け付けず
  `SignatureDoesNotMatch` を返す。資格情報の問題に見えるが違う (LIST と HEAD は同じ鍵で通る)。
  `request_checksum_calculation="when_required"` が要る (`collection/src/frostlog/upload/s3.py`)。
- **GCP の `display_name` は 100 バイト。** 文字数ではないので日本語だとすぐ超える。
- **通知は差分ではなく「窓の絵」。** イベント駆動だと同じ事実が何度も届く (当時の producer は
  直近 25 時間ぶんの upload_runs を返していたので、1 回の帰宅が最大 48 イベントに載った) ので、「もう言ったか」
  を覚える表が要り、その表の置き場所で本番が 403 で落ちた。定時に「発火時点から遡る窓」を出す
  形にすると、覚えるものが無くなる — **notifier は BigQuery に一切書かない**。帰宅後に数日ぶんが
  一気に届いても、その晩の窓に映るだけで「取りこぼし」という状態が存在しない。
- **Pi は冷蔵庫と一緒に車に載っている。** 外出中は自宅 Wi-Fi から離れるのでアップロードできない。
  下流をいくら速くしても、問 C の答えは**最後の観測からの外挿**にしかならない。観測の古さを
  見せること。
- **冷蔵庫が止まると温湿度も記録されない。** DHT20 はメッセージが届いた瞬間にだけ読む。
  周辺温度だけを独立に記録する経路は無い。
- **接続直後の `4402` は来ないとみなす。** 2026-09-14 に 5 分間 1 通も来なかった。状態が無いと
  設定温度も書けない (表示単位が分からない) ので、ゲートウェイはハンドシェイク後に `4040` を
  送って `4840` で取りに行く。`4840` の本文は `4402` と同じ配置なので、デコーダは 1 つでよい。
- **`4080` の設定温度は冷蔵庫の「表示中の単位」で解釈される。** °F 表示のときに a3=50 を書くと
  10 °C になった。書く前に最新の状態報告の表示単位を見て変換する。ここでも、状態を知らないうちは
  書かないという形になる。
- **バッチは日付ではなく boot の集合。** NTP 同期前の記録は時刻が狂っており、同じ boot の同期済み
  レコードが届くと**過去のレコードが別の JST 日付へ移動する**。
- **契約が「コピーは等価」を保証している。** 同じ (boot_id, uptime_seconds) が再送されても
  frames は一致する (`repeated_messages_agree`)。ただし**それ以外の列の一致は保証していない**
  ので、重複排除に `DISTINCT` を使ってはいけない。
- **datacontract-cli は gzip されたオブジェクトを読めない。** DuckDB エンジンは解凍するが、
  JSON Schema エンジンが生バイトを `json.loads` して死ぬ。バケットに対する検査は
  `--checks quality` に絞ってある (Issue #21)。

## 運用の約束

- **Pi への書き込み、fumo-terraform の PR マージ、古いオブジェクトの削除は、必ず事前に確認する。**
- Terraform の apply は GitHub Actions のみ。ローカルから apply しない。
- HMAC キー・Slack トークン・Wi-Fi の PSK は画面にもログにも出さない
  (`terraform output -raw` や `op read` から直接流し込む)。
- 無料枠のみ。Artifact Registry だけは、コンテナイメージの合計が枠に収まらないので超過を
  許容している。
- **`°C` と書く。`℃` は使わない。**

## 設計の決まりごと

- **収集は at-least-once、意味づけは exactly-once。** 重複排除は読み手 (semantics の staging) の
  仕事であって、収集側は同じものを二度送ってよい。
- **本番の dbt build は unit test を回さない** (`--exclude-resource-type unit_test`)。unit test は
  データではなく SQL の検査なので、SQL を変えた CI (`make -C semantics ci-warehouse`) が回す。
  定時に回しても守るものは無く、1 本 10 MiB の課金だけが増える。
- **ゲートウェイはディスクに書かない。** 記録も状態ファイルも持たない。接続が切れれば忘れる
  ものしか持たないので、再起動も落ちたことも「つながっていない」の一形態で済む。
- **collection は冷蔵庫に書けない。** コマンドの口を持たない。収集の責務に write は含めない。
- **コントローラーは在宅の変化 1 回につき 1 回だけ動く。** 次の変化まで、人がパネルで変えた
  設定が勝つ。毎分あるべき値に合わせる形にすると、人の操作を上書きし続けることになる。
- raw は届いたものをそのまま保つ。解釈できない行を落とすのは semantic モデルの側。
- `contracts/` が真実。dbt モデルも service もそこから導く (生成はしない)。
- ADR や設計ドキュメントはリポジトリに置かない。合意はセッションか GitHub の Issue/PR に残す。

## 書き方の決まりごと

**コードが語ることを、コメントやドキュメントに写さない。** 写した瞬間は正しくても、コードを
変えたときに一緒に直されない。この節を書く前には「Scheduler は 3 本」「job は semantics と
同じ image」「Slack #fumo に通知」がすべて嘘になっていた。

書かない:
- 数 (「service 2 本」「SA 11 個」)、cron 式、時刻、間隔、チャンネル名
- リソース名・ファイル名・環境変数名の**一覧** (ディレクトリや `.tf` の写し)
- 「いまは…」「現在は…」で始まる構成の説明
- 他のファイルの中身を引き写した相互参照 (「Terraform が X=Y を渡す」)。参照するなら役割で書く

書く:
- **なぜ**その形なのか。過去形の経緯はいくら書いてよい (過去は陳腐化しない)
- 1 ファイルを読んでも見えない**不変条件** (「notifier は BigQuery に一切書かない」)
- ユニットの名前と契約 ID。これは構成ではなく identity なので変わらない
- 契約の `slaProperties` が約束する頻度と遅延。これは構成の写しではなく consumer への約束で、
  契約だけが持てる。約束ではない実装の時刻や間隔 (「03:07 に走る」) は契約にも書かない
- 実行できるコマンド。動かなくなれば CI か手が気づく
