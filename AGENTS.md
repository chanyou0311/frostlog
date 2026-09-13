# AGENTS.md

車載のポータブル冷蔵庫 (Anker Solix EverFrost 2) が BLE で流す状態と、その周りの温湿度を
Raspberry Pi で記録し、バッテリー残量にまつわる問いに Slack で答えるためのリポジトリ。

**2 つのデータプロダクトと 1 つのデータアプリケーション**で構成し、境界はデータコントラクト
(`contracts/*.odcs.yaml`) が持つ。

| | 何を出すか | 実体 |
|---|---|---|
| `frostlog-collection` | 測ったものをそのまま | Pi の収集/送出 (`src/frostlog/`) → GCS |
| `frostlog-semantics` | 意味づけしたテーブル | dbt (`semantics/dbt/`) → BigQuery |
| データアプリケーション | Slack の通知 | `notifier/` → Cloud Run |

答えたい問いは 4 つ: A 残量が条件でどう変わるか / B 帰宅時の残量と翌朝の見通し /
C 外出中あと何時間もつか / D 設定温度に到達するまでの時間。

## データの流れ

```
Pi (frostlog-cooler.service : BLE を常時受信、周辺温湿度も同時に記録)
  │ frostlog-upload.timer  5 分ごと、前回の続きからバイト単位で差分を送出
  ▼
GCS  v1/{stream}/dt=YYYY-MM-DD/{offset:012d}.jsonl.gz   ← オブジェクト名は「ローカルファイルの何バイト目から」
  │ BigQuery Data Transfer Service  15 分ごと (バケットを自前で読む。リポジトリにコードは無い)
  ▼
BigQuery  frostlog.raw_cooler / raw_events   ← バケットの形そのもの。列を足さない
  │ Cloud Scheduler  frostlog-transform  1 時間ごと → POST /jobs/transform
  │   積まれた行数 (tables.get の num_rows) が gs://…/_control/built_through.json の
  │   目印より増えていたときだけ組む。増えていなければ何もしない
  ▼   dbt build 一式 (unit test は除く) → Pub/Sub frostlog-signals
Cloud Run service  frostlog-notifier  → Slack #fumo
  ▲ Cloud Scheduler  frostlog-weekly-deadline  月曜 21:03

Cloud Scheduler  frostlog-contract-test  毎日 03:07 → Cloud Run job  frostlog-contracts
```

## 必須コマンド

```bash
uv run pytest                                    # コレクター (リポジトリ直下が Pi のパッケージ)
make -C semantics check                          # lint + test + dbt parse (ウェアハウス不要)
make -C semantics ci-warehouse                   # 実 BigQuery で通し。ADC が要る
make -C semantics contract-test CONTRACT_ARGS="--contract collection"
scripts/deploy.sh                                # Pi へ配布 (既定 chanyou@192.168.100.40)
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
  23,861 キーまで膨らんだ。実行依存の値を parse 解決される config に入れてはいけない。いまは
  全テーブルを毎回作り直す (`+materialized: table`) ので MERGE 自体が無い。
- **ロードジョブは、自分のスキーマが名前を挙げた列に DEFAULT を入れない。** NULL を入れる。
  Data Transfer Service は常に宛先の全列を名前で挙げるので、`DEFAULT CURRENT_TIMESTAMP()` は
  **DTS 経由では絶対に発火しない**。`load_table_from_uri` で手元から試すと動くので気づけない
  (本番 31,966 行が全て NULL になった)。取り込み時刻が要るなら、列ではなく**テーブルの
  メタデータに訊く** — `tables.get` は無料で、`num_rows` は行が実際に積まれたときだけ動く
  (`modified` は 0 行のロードでも動いてしまうので使えない)。
- **CI がサンプルを一括で配信すると、実運用のチャンク境界を踏めない。** チャンクは 5 分ごとに
  切れるので、境界の直前の報告は**その続きを別のチャンクから受け取る**。`ci-warehouse` は
  サンプルを 2 つに割って配信し、両方が届いてから 1 回だけ組んで `held_seconds` を契約に
  当てる (`semantics/Makefile`)。配信の途中で組んでも、全テーブルを作り直す以上は何も増えない。
- **botocore 1.36 以降は PUT に CRC32 を付ける。** GCS の S3 互換 API はこれを受け付けず
  `SignatureDoesNotMatch` を返す。資格情報の問題に見えるが違う (LIST と HEAD は同じ鍵で通る)。
  `request_checksum_calculation="when_required"` が要る (`src/frostlog/upload/s3.py`)。
- **GCP の `display_name` は 100 バイト。** 文字数ではないので日本語だとすぐ超える。
- **Pi は冷蔵庫と一緒に車に載っている。** 外出中は自宅 Wi-Fi から離れるのでアップロードできない。
  下流をいくら速くしても、問 C の答えは**最後の観測からの外挿**にしかならない。観測の古さを
  見せること。
- **冷蔵庫が止まると温湿度も記録されない。** DHT20 は BLE メッセージを受けた瞬間にだけ読む
  (`EverfrostReceiver._message`)。周辺温度だけを独立に記録する経路は無い。
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
- 無料枠のみ。Artifact Registry だけは datacontract-cli の依存 (pyarrow で 124 MB) が重く、
  超過を許容している。
- **`°C` と書く。`℃` は使わない。**

## 設計の決まりごと

- **収集は at-least-once、意味づけは exactly-once。** 重複排除は読み手 (semantics の staging) の
  仕事であって、収集側は同じものを二度送ってよい。
- **本番の dbt build は unit test を回さない** (`--exclude-resource-type unit_test`)。unit test は
  データではなく SQL の検査なので、SQL を変えた CI (`make -C semantics ci-warehouse`) が回す。
  毎時回しても守るものは無く、1 本 10 MiB の課金だけが増える。
- raw は届いたものをそのまま保つ。解釈できない行を落とすのは semantic モデルの側。
- `contracts/` が真実。dbt モデルも service もそこから導く (生成はしない)。
- ADR や設計ドキュメントはリポジトリに置かない。合意はセッションか GitHub の Issue/PR に残す。
