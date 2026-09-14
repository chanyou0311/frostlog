# frostlog-controller

在宅かどうかで冷蔵庫の設定温度を切り替える Pi 上のジョブ。外部入力の無い夜を
−20 °C のまま過ごすと電池が尽きる一方、帰宅のたびにパネルで手を動かす運用は
続かない。覚えるのは在宅/外出の 1 ビットだけで、それが変わった瞬間に
`frostlog-gateway` (`../gateway/`) へ設定温度のコマンドを 1 回だけ送る。
「あるべき値に毎分合わせる」形にしないのは、それだと人がパネルで変えた設定が
次の切り替えまで勝つという約束も、冷蔵庫の状態通知の頻度も保てないため。
経緯は Issue #38。

標準ライブラリだけで書いてある。Pi Zero (1 コア) の起動コストに対して
pydantic や typer の import だけで数秒かかり、このジョブは短い間隔で起動する
ため見合わない。

## 使い方

```sh
cd controller
make install   # uv プロジェクト (開発用の venv)
make check     # ruff / ty / pytest
```

設定 (家の SSID、切り替え先の設定温度、諦めるまでの試行回数) は TOML
(`scripts/controller.toml.example`)、判断は JSON 1 ファイルに保存する。
既定のパスはどちらも XDG のディレクトリ規約に従い、環境変数で上書きできる。

Pi には Mac から `make deploy` (`PI=user@host` で宛先を変える) で配る。
uv も venv も持ち込まず、OS の `/usr/bin/python3` を `PYTHONPATH` 経由で
そのまま動かす。ログは `journalctl --user-unit frostlog-controller` で見る。
