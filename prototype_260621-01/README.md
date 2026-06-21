# Sim Report Multiview — 試作 (SPEC_260621-01)

チャートビュー下部にシミュレーション結果レポートをマルチ画面表示し、チャートと
双方向に連動させる試作。**使い捨て試作**（恒久品質は問わない）。既存データは読むだけ・無改変。

## 仕様 4 点の対応

| 仕様 | 実装 | 連動 |
|---|---|---|
| 1. レポートのマルチ画面 + インタラクティブ接続 | 下部タブ（明細/ヒートマップ/グラフ/サマリー） | 全タブがチャートと連動 |
| 2. 取引明細 ⇔ 売買ペア連動 | ソート可能テーブル（5,224 取引） | 行 hover→**該当ペアの建玉〜決済区間だけ明色、他のローソク足を減光**（既存 `trade_markers_renderer.js` 仕様踏襲・DIM_ALPHA=0.15）＋非ペアマーカーも減光 / マーカーグリフ hover（`hoveredObjectId` 命中・時間軸近接ではない）→該当行ハイライト＆同減光、グリフ外は解除 |
| 3. 最小単位項目をテーブル＋ヒートマップ | 曜日×時間帯の損益ヒートマップ（115 セル） | セルクリック→該当バケットの取引を抽出しチャート＆明細でハイライト |
| 4. グラフ項目を全てインタラクティブ化 | 10 グラフ（下記）Chart.js | 点クリック→該当取引 / 棒クリック→バケット抽出 / 資産曲線→その時刻へ移動 |

10 グラフ: Balance / Entries by hours(Asia,Europe,USA) / Entries by weekdays / Entries by months /
P&L by hours / P&L by weekdays / P&L by months / Correlation(Profits,MFE) /
Correlation(Profits,MAE) / Position holding time。

## データ（実データ・無改変で読込）

- バー: `simulator/.../2026-04_stop-probe_oos/bars_m1_is.csv`（M1, 22,771 本）
- オラクル: `ReportTester-900005560_2604_03.xlsx`（Deals/Orders/メトリクスを抽出）
- 整合性: 取引 5,224・純損益 +11,370・最終残高 21,370（オラクル一致）
- MFE/MAE はバーを走査して算出（point=0.1 JPY 換算）

## 実行

```bash
cd /workspaces/app
PYTHONPATH=/workspaces/app python3 prototype_260621-01/prep_data.py   # data.json 生成（要 openpyxl）
cd prototype_260621-01 && python3 -m http.server 8765
# ブラウザで http://localhost:8765/index.html
```

検証（ヘッドレス・スクショ）: `python3 prototype_260621-01/verify.py` → `shots/`。

## 構成
- `prep_data.py` … xlsx+csv → `data.json`
- `index.html` … 単一ファイル UI（`vendor/lightweight-charts`, `vendor/chart.umd.js`）
- `verify.py` … Playwright 自動検証
- `shots/` … 検証スクリーンショット

## 試作の割り切り（既知の限界）
- マーカーは可視範囲の取引が 700 件以下のとき描画（全期間ズーム時は「ズームイン」表示）。
- 明細テーブルは 5,224 行を全描画（仮想化なし）。
- セッション境界（Asia/Europe/USA）は UTC 固定の簡易区分。
