# OOS ホワイトチェック — 試作（IS/OOS 検証）

戦略の **過剰最適化（カーブフィッティング）** を、同一パラメータの IS（学習区間）と
OOS（検証区間／forward）の並列比較で定量判定する使い捨て試作ダッシュボード。
既存データは読むだけ・無改変。**使い捨て試作**（恒久品質は問わない）。

## 何を検証するか

`.doc/ISOOS_SIMPLE_SPLIT_BASIC_DESIGN.md` の単純分割（FR-04 並列レポート／FR-05 劣化指標）の
最小可視化。MT5 が 2026-04 を IS(04.01-14) / forward=OOS(04.15-) に分割した
`2026-04_stop-probe_oos` の**両オラクルが bit-exact 検証済み**である点を土台に、
両区間の成績を読み取り並べて「未知区間で優位性が崩れるか」を見る。

| 区間 | オラクル xlsx | 期待（trades / net / balance） |
|---|---|---|
| IS（学習 04.01-14） | `ReportTester-900005560_2604_03.xlsx` | 5224 / +11,370 / 21,370 |
| OOS（検証 04.15-23） | `ReportTester-900005560_forword_01.xlsx` | 2438 / −4,020 / 5,980 |

## 表示内容

- **判定バナー**：`過剰最適化 / 要注意 / 合格`（規則は `prep_data.py` の verdict 部）。
- **価格チャート（ローソク足）**：全期間 03-23〜04-29（`bars_m1.csv`・37,736本）。建玉マーカー
  IS=青/OOS=橙・▲買▼売、分割(04-15)マーカー。表示範囲の建玉が cap(900) 以下のとき描画（ズームで表示）。
- **エクイティ曲線**：両区間とも初期 10,000 から。縦点線 = 分割(04-15)。IS=青 / OOS=橙。
- **劣化比較表**：純損益・PF・勝率・期待値・ペイオフ・リターン・最大DD を IS|OOS|比(OOS/IS)|Δ で。
- **区間サマリー KPI ＋ オラクル突合**（算出値 vs 実 MT5 の一致 ✓）。
- **純損益内訳**：総利益/総損失/純損益の区間別棒グラフ。

## 現在の判定結果

**過剰最適化（fail）**：IS 黒字(+11,370) に対し OOS 赤字(−4,020)。PF 1.159→0.888（比0.766）、
期待値 +2.18→−1.65（正→負反転）、勝率 −5.47pt、最大DD −7.97%→−41.54%。
＝この戦略パラメータは学習区間限定で、未知区間では優位性を失う。

## 実行

```bash
cd /workspaces/app
PYTHONPATH=/workspaces/app python3 prototype_260623-01/prep_data.py   # data.json 生成（要 openpyxl）
cd prototype_260623-01 && python3 -m http.server 8766
# ブラウザで http://localhost:8766/index.html
```

検証（ヘッドレス・スクショ）: `python3 prototype_260623-01/verify.py` → `shots/dashboard.png`。

## 構成
- `prep_data.py` … 両オラクル xlsx ＋ `bars_m1.csv` → `data.json`（区間別指標＋劣化指標＋判定＋全期間バー＋建玉マーカー）
- `index.html` … 単一ファイル UI（`vendor/chart.umd.js` ＋ `vendor/lightweight-charts.standalone.js`）
- `verify.py` … Playwright 自動検証

## 試作の割り切り（既知の限界）
- IS と OOS は別 run（両方 10,000 始点）。時間軸上で IS 終端(21,370)と OOS 始点(10,000)に
  視覚的な段差が出るが、これは「同一資金から再評価した別区間」のため正しい挙動。
- 指標は決済 deal の Profit/Balance から算出（バー走査の MFE/MAE は本ツールでは扱わない）。
- 判定規則は最小ヒューリスティック（OOS赤字かつIS黒字→fail 等）。閾値は `prep_data.py` 参照。
