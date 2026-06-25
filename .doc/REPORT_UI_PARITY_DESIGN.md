# REPORT_UI パリティ詳細設計書

試作 `prototype_260623-02`（=**正**・単一HTML `index.html` 1201行）へ現行モジュール実装
`simulator/report_ui/web/` を**完全準拠**させる内部設計。目的は「実装で1点も抜けない」
受入基準付きの、試作→現行モジュール完全マッピング。

- 一次情報（正）: `prototype_260623-02/index.html`（inline CSS L8-173 / HTML L175-264 / JS L266-1199）, `README.md`
- 改修対象（現行）: `simulator/report_ui/web/{index.html,css/style.css,js/*.js}`
- 視覚オラクル: `prototype_260623-02/shots/*.png`
- データ: `simulator/report_ui/web/data/report.json`（**無改変・読むだけ**・両区間payload込み）

## 0. データ契約の実証（波及厳禁の担保）

全18点は既存 `report.json` フィールドのみで実装可能（grep実証済・データ層改修不要）。
- `segments.{is,oos}.agg.balance_curve [{time,value}]` → Balance/DD窓(1,2)・cmpDD(12)
- `segments.{is,oos}.agg.{scatter_mfe,scatter_mae,hold_pl,weekorder,heat,...}` → graphs/heat（既存）
- `summary.{is,oos}.{net,win_rate,profit_factor,expectancy,payoff,return_pct,max_dd_pct,final_balance,trades}` → radar(13)/deg(14)/カード
- `degradation.<k>.{is,oos,ratio,delta}` → 7指標カード（既存）/deg(14)
- `segments.{is,oos}.report{}` → Report タブ(8)・比較表（既存）
- `meta.{initial_deposit,split}` / `segments.*.{label,meta}` → エクイティ/DD縦線・ヘッダ
- 注: 現行 `summary.is.max_dd_pct=-11.52`（負値）。radar の低DD軸は `Math.abs()` で符号非依存（点13踏襲）。

## 1. モジュール責務配分

| 領域 | 配分先 | 根拠 |
|---|---|---|
| チャート多窓（1-4,7） | `chart.js`（大改修） | lwc 3インスタンス・同期・減光は chart 責務 |
| 最大化/リサイザ/レイアウト（5,6,10,11） | 新規 `js/layout.js` | レイアウト状態機械を main から分離 |
| Report タブ（8） | 新規 `js/report.js` | REPORT_GROUPS 章立て描画 |
| Glossary タブ＋tip（9） | `glossary.js`（buildGlossary/wireTips 追加） | 用語データは glossary.js |
| 比較追加チャート（12-14） | `compare.js`（_renderCharts 拡張） | cmpCharts 隔離 init 踏襲 |
| 区間トグル/ラベル/カード削除（15,16,17） | `index.html`+`main.js`(+css) | DOM 構造とブート結線 |
| フィルタ解除ピル/件数（18） | `index.html`+`main.js`（linkage 既存活用） | applyFilter は既存 |

- vendor 無断バージョン変更禁止（lightweight-charts v4.1.3 / chart.umd v4.4.1）。
  試作が同系 vendor で addAreaSeries/addBaselineSeries/setVisibleLogicalRange/
  subscribeCrosshairMove/setCrosshairPosition/clearCrosshairPosition を実挙動＝v4.1.3で可。
- 新規: `js/layout.js`, `js/report.js`, `tests/e2e/verify_parity.py`。
- 改修: index.html, css/style.css, chart.js, main.js, compare.js, glossary.js, format.js(fmtT追加), 既存 e2e。
- 削除: `#summary-cards` ブロック＋renderSummary/SUMMARY_FIELDS＋css `.cards`（点17）。

## 2. 18点 点ごと設計＋受入基準（試作行 → 現行改修 / shot）

| # | 点 | 試作行 | 現行改修 ファイル:関数 | 受入基準（観測可能）/ shot |
|---|---|---|---|---|
| 1 | Balance（資産曲線）窓 | L424,449-452 | chart.js:_buildBalanceSeries（addAreaSeries・フィル＋縦グラデ・低α） | #paneBal に資産曲線。終値=final_balance / sep_windows.png |
| 2 | Drawdown（残高DD・JPY）窓 | L425,454-458 | chart.js:_buildDrawdownSeries（addBaselineSeries・0基準・下方フィル） | #paneDD にアンダーウォーター曲線（≤0）/ dd_final.png |
| 3 | 3窓の論理レンジ同期 | L460,479-489 | chart.js:syncCharts（subscribeVisibleLogicalRangeChange→setVisibleLogicalRange相互） | 1窓ズーム/パンで全窓の時間軸一致 / panes_zoom.png |
| 4 | クロスヘア同期 | L462-509 | chart.js:crosshairSync（subscribeCrosshairMove→setCrosshairPosition他窓） | 1窓hoverで全窓に同時刻縦線 / crosshair_sync.png |
| 5 | 高さ可変 rz1/rz2 | L353-373 | layout.js:makeResizer（ドラッグでpane高さ） | rz1=ローソク/Bal境界, rz2=Bal/DD境界 ドラッグで可変 / resized.png |
| 6 | ⛶チャート最大化 | L323-350 | layout.js:applyLayout('chart')（savedCwPx退避・復元で高さ保持） | ボタンで上部全画面・再クリック復元 / chart_max.png |
| 7 | chartBadge | L516-525 | chart.js（可視レンジ→価格/時刻 readout） | #chartBadge に値表示 / verify.png |
| 8 | サマリー(Report)タブ | L899-921 | 新規 report.js:buildReport（区間別 report dict を章立て） | #pane-report に区間別フルreport / report_table3.png |
| 9 | 用語説明(Glossary)タブ＋tip | L301-321,923-1025 | glossary.js:buildGlossary/wireTips | #pane-glossary 表示＋data-gg hover tip / tooltip.png |
| 10 | ⛶明細最大化 | L343 | layout.js:applyLayout('detail')（下部全画面・グラフ100%充填） | ボタンで下部全画面・復元 / detail_max.png |
| 11 | rz0＋3状態レイアウト | L324-346 | layout.js（body flex 再編・normal/chart/detail） | チャート/下部の分割ドラッグ＋3モード / split_layout.png |
| 12 | cmpDD（最大DD・縦線04-15） | L1137-1153 | compare.js:_renderCharts（cmpDD＋split縦線） | 比較タブ右に残高DD曲線＋分割縦線 / dd_final.png |
| 13 | cmpRadar（レーダー） | L1155-1172 | compare.js（metricRetention・7軸 IS/OOS） | 比較タブ右にレーダー / radar_tall.png |
| 14 | cmpDeg（劣化チャート） | L1173-1182 | compare.js（degradationBars） | 比較タブ右に劣化棒 / deg.png |
| 15 | 区間トグルボタン | L178-182,408-412 | index.html segwrap(.segbtn)＋main.js（select廃止） | ヘッダがトグル(IS 学習/OOS 検証) / verify.png |
| 16 | 選択ラベル hSel | L572-577 | index.html #hSel＋main.js subscribeFilter | 連動選択ラベル表示 / verify.png |
| 17 | 最上部サマリーカード削除 | （試作に無し） | index.html/main.js/css から #summary-cards・renderSummary・.cards 除去 | 最上部カードが存在しない / verify.png |
| 18 | フィルタ解除ピル＋件数 | L584-602 | index.html #clearFilter/#detailCount＋main.js | フィルタ時にピル＋件数、✕で解除 / nup_table.png |

## 3. 状態遷移・データフロー

- boot: fetch→DATA→renderVerdict→hover/filter結線（hSel/pill）→buildCompare（dd/radar/deg追加）
  →buildGlossary/wireTips→wireTabs(6タブ)→layout.wireResizers/wireMaximize→buildSegToggle→selectSegment('is')。
- selectSegment(seg): filter解除→tradesById→ヘッダmeta/トグルactive→renderChart(多窓・destroy→再生成)
  →renderTable/Heatmap/Graphs→buildReport(seg)→resize。buildCompare/buildGlossary は seg非依存（init1回）。
- layoutMode ∈ {normal, chart, detail}（chart=bottom非表示・savedCwPx退避 / detail=chartWrap非表示・グラフ充填 / 復元=保持高さ）。

## 4. テスト設計

- 単体(node:test): underwaterCurve/drawdownSeries・metricRetention・degradationBars・reportRowsModel・
  balanceForwardFill・byTimeResolve・GLOSSARY網羅。純ロジックはDOM非依存でexport。
- E2E: 新規 `tests/e2e/verify_parity.py`（18点×最低1 assertion・`window.__balChart`/`__cmpCharts` フック・shots併記）。
- 既存e2e改修（必須・点15/17でDOM依存が破綻）: verify.py(#summary-cards→#cmpBasic / #seg-select→.segbtn),
  verify_compare.py(select_option→segbtn click＋右グラフ5本化), verify_graphs/heatmap/table.py(seg切替をclick化)。

## 5. 制約・リスク

- report.json 無改変（read-only）。back集計・データ層への波及ゼロ。
- vendor バージョン固定。baseline/area の option 細部（topColor/bottomColor/lineColor等）は v4.1.3 API で実装時確認。
- body flex 再編（点11）は DOM 構造変更。試作の実挙動を正に一意確定。
- クロスヘア同期 E2E は byTimeResolve 純関数を node:test で確証＋E2E は縦線存在の弱検証。

## 6. 受入（Definition of Done）

- [ ] 18点すべて実装（上表 受入基準を満たす）。1点も欠落なし。
- [ ] #17: 最上部サマリーカードが DOM に存在しない（完全準拠）。
- [ ] verify_parity.py が18点を緑で通過＋既存e2e改修後も全緑。
- [ ] report.json 無改変（git diff空）。既存テスト回帰なし。
