# frostlog-contracts

`*.odcs.yaml` がデータ契約そのもの（スキーマの正）で、`job/` は本番のデータをその契約に
当てる Cloud Run job（`frostlog-contracts`）。どちらのデータプロダクトにも属さない — 両方が
約束を守っているかを確かめる側なので、自分の image を持ち、単独でデプロイする。

```sh
cd contracts
make install                    # job の uv プロジェクト（datacontract-cli を含む）
make check                      # ruff / ty / pytest — データ不要
make lint                       # 契約そのものが well-formed か
make test                       # 本番のデータを契約に当てる（ADC が要る）
make test CONTRACT=collection   # 片方だけ
```

設定は環境変数（Terraform が Cloud Run job に与える）。`FROSTLOG_BQ_DATASET`、
`FROSTLOG_SIGNALS_TOPIC`（トピックの短い名前）、`FROSTLOG_GCP_PROJECT`（省略時は
Application Default Credentials のプロジェクト）。healthchecks.io の URL とバケットの HMAC 鍵は
Secret Manager にあり、`FROSTLOG_CONTRACT_TEST_HEALTHCHECK_URL_SECRET` と
`FROSTLOG_COLLECTION_HMAC_SECRET` にはその名前だけを渡す（読めなければ ping と収集契約の検査を
飛ばす）。
