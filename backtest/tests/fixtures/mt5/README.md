# MT5 突合テストケース (`fixtures/mt5/`)

MT5 ストラテジーテスターの実出力を **ケース単位の自己完結 fixture** として保管する。
バックテスト計算ロジック (`compute_stats` 等) が MT5 実測値を再現することを golden 突合する用途。

## ディレクトリ構成

各ケースは 1 ディレクトリ = 1 (EA × 銘柄 × 期間 × 実行条件) で完結する。

```
fixtures/mt5/
├── __init__.py                      # 統一ローダ (load_case / list_cases)
├── README.md                        # このファイル
└── <case_name>/                     # 例: ma_slope_jp225_202501
    ├── case.yaml                    # run config + 銘柄仕様 + EA入力 + 期待サマリー (人が読むメタ)
    ├── input/
    │   └── *.csv                    # MT5 エクスポートの価格データ (タブ区切り)
    ├── expert/
    │   └── *.mq5                    # EA 原典ソース
    ├── mt5_report/
    │   ├── ReportTester-*.xlsx      # テスターレポート (原本)
    │   ├── tester.log               # テスター全ジャーナル
    │   └── settings.jpg             # 設定スクリーンショット
    └── expected/
        └── report.json              # パース済み正解 (settings / results / deals)。golden の最終オラクル
```

## 各ファイルの役割

| パス | 役割 |
|---|---|
| `case.yaml` | 銘柄仕様・口座・EA 入力・実行条件・期待サマリーの **人が読む要約**。数値の最終正は `expected/report.json`。 |
| `input/*.csv` | テスターに与えた価格データ (MT5 エクスポート)。再実行・再検証用。 |
| `expert/*.mq5` | 突合対象 EA のソース原典。ロジック確認用。 |
| `mt5_report/` | テスターレポート原本・ジャーナル・設定スクショ。一次証跡 (人が参照)。 |
| `expected/report.json` | `report.json` をパースした **正解データ**。`settings` (実行条件) / `results` (STAT_* 実測値) / `deals` (全 deal 明細) を含み、golden テストが参照する唯一のオラクル。 |

## ローダの使い方

```python
from backtest.tests.fixtures.mt5 import load_case, list_cases

list_cases()                 # -> ["ma_slope_jp225_202501", ...]

case = load_case("ma_slope_jp225_202501")
case.dir                     # ケースのルート Path
case.config                  # case.yaml の dict
case.input_csv               # 価格データ CSV の Path (input/*.csv)
case.expert_mq5              # EA 原典の Path (expert/*.mq5)
case.expected                # report.json の dict (source/settings/results/deals_count/deals)
case.deals                   # case.expected["deals"] のショートカット (list[dict])
```

パスはすべて `__file__` 基準で解決するため **cwd に依存しない**。

## 新ケースの追加手順

1. `fixtures/mt5/<新ケース名>/` を作り、上記構成のサブディレクトリ
   (`input/` `expert/` `mt5_report/` `expected/`) を作成する。
2. MT5 出力をリネームして配置する:
   - 価格 CSV → `input/<銘柄>_<足>_<期間>.csv`
   - EA ソース → `expert/<EA名>.mq5`
   - テスターレポート → `mt5_report/ReportTester-*.xlsx`、ジャーナル → `mt5_report/tester.log`、設定 → `mt5_report/settings.jpg`
   - パース済み正解 → `expected/report.json`
3. `case.yaml` を作成する (銘柄仕様・口座・EA 入力・実行条件・期待サマリー)。
   数値の最終正は `expected/report.json` の `results` であることを注記する。
4. `git check-ignore <新パス>` で全ファイルが追跡可能 (ignore されない) ことを確認する。
   `fixtures/` は `.gitignore` で除外解除済 (`!backtest/tests/fixtures/**`)。
5. `list_cases()` に新ケースが現れること、`load_case("<新ケース名>")` が通ることを確認する。

## ケース一覧

| ケース名 | EA | 銘柄 | 期間 / 足 | 概要 |
|---|---|---|---|---|
| `ma_slope_jp225_202501` | MA_Slope_EA | JP225 | 2025.01 / M1 (1 minute OHLC) | net -6169 / 1163 trades / PF 0.630045 / stop-out。`compute_stats` の golden 突合に使用。 |
