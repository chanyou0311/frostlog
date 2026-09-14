# frostlog-semantics

BigQuery の raw テーブルを dbt でディメンショナルモデルに変換する Cloud Run サービス
（`frostlog-semantics`）。バケットから raw への取り込みは BigQuery Data Transfer Service の
仕事で、このリポジトリにコードは無い。`service/` が Python、`dbt/` が
dbt プロジェクト、スキーマの取り決めは `../contracts/semantics.odcs.yaml`。

```sh
cd semantics
make install        # service の uv プロジェクト（dbt を含む）
make check          # ruff / ty / pytest / dbt parse — BigQuery なしで通る
make build          # 全期間を作り直す（BigQuery が要る）
make unit-test      # dbt のユニットテスト（同上）
make ci-warehouse   # ../contracts/samples から CI データセットを作り直して契約を検査する
```

設定は環境変数（Terraform が Cloud Run に与える）。`FROSTLOG_COLLECTION_BUCKET`（どこまで組んだ
かの目印 `_control/built_through.json` を置くバケット）、`FROSTLOG_BQ_DATASET`、
`FROSTLOG_BQ_DATASET_CI`、
`FROSTLOG_SIGNALS_TOPIC`（トピックの短い名前）、`FROSTLOG_GCP_PROJECT`（省略時は
Application Default Credentials のプロジェクト）。
