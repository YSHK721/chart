# prototype_260626-01 — 因果リビール再生（tgp_btlm）

indicator_ui「時間軸リビール型再生（戦略立案の事前分析・検証）」の使い捨て試作。
**既存データ・既存コードへの波及ゼロ**（既存 jp225 日足を読み取り専用で `data.json` にエクスポート）。

## 何を確認できるか
- **ルックアヘッド排除の因果再生**: t を進めると足が左→右に増え、各 t の tgp_btlm 帯は `df[:t+1]` で再計算した as-seen-at-t 値（将来リーク無し）。
- **直近窓追従トグル**: 大きい t で帯が潰れる問題（全体 fitContent）と、直近窓に追従ズームする改善の比較。
- **決定点リードアウト（能力A）**: t 時点の OHLC＋帯値＋「close vs mean → buy候補/見送り」。
- **リペイント比較（能力B）**: 最終確定帯をゴースト重ね＋過去座標の乖離量（%）を表示。

## データの素性（決定性・ライブ同一条件）
- `data.json` は実 TGP(MCMC)・`seed=20260101` 固定・preset=standard・maxbars=100 で事前計算。
- 各フレーム = `untilTime=df[:t+1]` 再計算のサンプル（`frame_step` 間引き）。`final` は untilTime 無し（リペイント比較用）。

## 使い方
```
# 1) data.json 生成（既存データ読み取りのみ・実TGP・rpy2必要）
python3 prep_data.py                 # → data.json
# 2) 配信
python3 serve.py 8791                 # → http://127.0.0.1:8791/
# 3) 自動検証（別プロセスで serve 起動後）
python3 verify.py 8791                # → shots/*.png
```

## ファイル
| ファイル | 役割 |
|---|---|
| index.html | 再生UI（足リビール＋帯＋追従トグル＋リードアウト＋リペイント比較）。data.json を静的描画。 |
| prep_data.py | 既存 jp225 日足を読み取り→実TGP再計算→data.json 出力。 |
| serve.py | 静的配信（no-store）。 |
| verify.py | playwright で複数フレーム/モードを撮影→shots/。 |
| data.json | 事前計算データ（candles/frames/final）。 |
| shots/ | 検証スクリーンショット。 |

関連: `.doc/indicator-management-ui/仕様書_チャート再生機能_時間軸リビール.md`
