# tickvol_updown — アーカイブ（UI 未結線）

本パッケージは **UI から外してある**（ISSUE-244・2026-08-02）。計算コードとその単体テストは
そのまま残してあるが、`indicator_ui` の結線には一切登録されていないため、UI・API のどちらからも
呼ばれない。単体テスト（`tests/test_tickvol_updown.py`・11 件）はパッケージ内で完結しており通る。

## 何をする指標だったか

各足の方向内訳（`up`＝上昇ティック数 / `dn`＝下落ティック数）を直近 `window_n` 本ぶん合計し、
その差（上昇 − 下落）を 1 本のバーで描く。ゼロ起点で、正なら緑・負なら赤。

## なぜ外したか（実測）

- **上昇と下落がほぼ鏡像**（ISSUE-241）。移動累積の相関 0.9993〜0.9999、全期間累積 1.000000、
  最終差 0.188%。2 本並べると読めないため 1 本（差）へ変更したが、
- **その差はコイン投げ以下**（分散比 0.83〜0.97）。符号付きティックに方向情報が無いことは
  ISSUE-241 の 18 条件の実測で確定済み（宣言時の的中率 41.9% に対しベース率 49.8%）。
- 使い道が実測で見つからないまま UI の選択肢だけが増えるため、ISSUE-244 で整理した。

## 残してあるもの

| 対象 | 状態 |
|---|---|
| `src/` `tests/`（本パッケージ） | そのまま |
| `up` / `dn` 列（`marketdata/csv_schema.py` の `UPDOWN_COLUMNS`） | **残す**。データ基盤は撤去しない |
| 再生成済み CSV（`jp225_tick_m1.csv` / `rollups/jp225_tick/*`） | **残す**（up/dn 列を含んだまま） |

## 復活させるときに触るファイル

結線は以下に閉じている（`grep -rn tickvol_updown` の全ヒット）。

| 層 | ファイル | 追加する内容 |
|---|---|---|
| back | `indigators/indicator_ui/api/adapter/compute/call_binding.py` | `_TABLE` へ `("tickvol_updown","default")`（`loader` / `output_kind:"histogram"` / `kind:"kw"` / `latest_meta` / `params_defaults:{"window_n":20}`）。`latest_meta` は `("window", max(window_n,1)+8, 1)` — 余裕 8 本は `latest_dispatch` の min_tail（欠落閉周期 5 + 形成中 1 + 1）を賄う |
| back | `api/tests/golden/catalog_defaults.json` | `"tickvol_updown": {"window_n": 20}` |
| back | `api/tests/test_solid_binding_spec_guards.py` | `_LATEST_GOLDEN_EMPTY_PARAMS` へ `("tickvol_updown","default"): ("window", 28, 1)` |
| front | `web/js/usecase/catalog.js` | `IndicatorDef`（`placement:'pane'` / `series:[PF_HIST('tickvol_updown')]` / `param('window_n', INT, 20, isPeriod:true)`）と `REGISTRY` への追加 |
| test | `web/tests/{catalog,catalog_client,facade,period_param_flags}.test.js` | 指標件数（26 → 27・`tab:'indicator'` 24 → 25）と `EXPECTED_PERIOD_PARAMS` |
| test | `simulator/replay_ui/web/tests/{catalog_client,facade}.test.js` | 同じ件数（`catalog.js` 自体は symlink なので本体は不要） |

`web/js/usecase/intrabar_forming_ids.js` には**登録しない**。形成中バーは方向内訳を持たない
（`forming_bar` は OHLCV のみ）ため、足内では窓に NaN が入り累積が消える。

## 注意

`up + dn` は `volume` より「分数」だけ小さい。方向は `sign(mid_i − mid_{i−1})` を
**その分バーの中で**取る仕様で、各分の先頭ティックが方向を持たないため（チャンク独立性の契約・
`marketdata/tick_m1.py:116-121`）。等値ティックはどちらにも数えない（実測 0.0%）。

---

# tickvol の回帰トレンド（`indigators/tickvol/src/trend.py`）— 同じく UI 未結線

ISSUE-244 で `tickvol` から回帰トレンド（btlm_trail 仕様の参照拡張・ISSUE-240）を外した。
`trend.py` と単体テスト `indigators/tickvol/tests/test_trend.py` はそのまま残してある。

## 外した系列・パラメータ

| 区分 | 名前 |
|---|---|
| 系列 | `tickvol_trend_mean` / `tickvol_trend_q{pct}`（動的）/ `tickvol_trend_off_hi` / `tickvol_trend_off_lo` / `tickvol_trend_beta` / `tickvol_trend_sigma` / `tickvol_trend_band_hit_rate` |
| パラメータ | `maxbars` / `band_method` / `empirical_n` / `show_metrics` / `n_cov` |

`tickvol` に残したもの: 本体ヒストグラム、正常帯 `tickvol_q{pct}`、
外れ値水準 `tickvol_evq_med_hi` / `_evq_ext_hi` / `_gpd_hi`、
パラメータ `window_n` / `q_low` / `q_high` / `q_out` / `k_events`。

## 復活させるときに触るファイル

| 層 | ファイル | 戻す内容 |
|---|---|---|
| 指標 | `indigators/tickvol/src/__init__.py` | `.trend` の再エクスポート（`tickvol_trend` / `TREND_KEYS` / `BAND_METHODS` / `DEFAULT_BAND_METHOD` / `DEFAULT_MAXBARS` / `DEFAULT_EMP_N` / `DEFAULT_N_COV`） |
| 指標 | `indigators/tickvol/src/lwc_chart.py` | `add_tickvol` の 5 引数、`_TREND_*` 色定数と `_TREND_POINT_RADIUS`、`_trend_quantile_series_name`、`_emit_hinted`、トレンド emit ブロック（`numpy` の import も要る） |
| back | `api/adapter/compute/call_binding.py` | `("tickvol","default")` の `params_defaults` へ 5 個 |
| back | `api/adapter/compute/incremental/tickvol.py` | `_TREND_FIXED_NAMES` / `_TREND_KEYS`、`_Request` の 5 フィールド、`_State.deviations` / `.trend`、`_prepare` の検証、`build` の `tickvol_trend` 呼び出し、`_extend` / `adapt` の引き回し、`_trend_at`、`emit` のトレンド節（帯は 2 本 → 4 本）、ヘルパー `_trail_src` / `_trend_q_out` / `_deviation` / `_append_trend` |
| back | `api/tests/golden/catalog_defaults.json` | `tickvol` へ 5 個 |
| front | `web/js/usecase/catalog.js` | `TICKVOL` の params 5 個と series 3 ブロック |
| test | `indigators/tickvol/tests/test_tickvol.py` | `add_tickvol` の戻り本数 6 → 14 |
| test | `api/tests/test_tickvol_binding.py` | `_SERIES_NAMES` へトレンド 8 本、トレンド系のテスト |
| test | `web/tests/tickvol_catalog_entry.test.js` | series 数 5 → 12、params 5 → 10、動的名 1 → 2 |
| test | `web/tests/period_param_flags.test.js` | `tickvol: ['window_n', 'maxbars', 'empirical_n', 'n_cov']` |

## 外す前の実測（`trend.py` の docstring に全文あり）

既定を btlm_trail 本体（名目 ols）と変えて経験分位にしていたのは実測根拠による。
tick 数の乖離率は右に強く歪み（歪度 5m +35.5 / 15m +4.35 / 1h +2.15）、名目 ols では
最大 57.5% のバーで帯の下端が「tick 数として成立しない値（1 未満）」になる。
復活させるならこの既定（`band_method="empirical"`）を維持すること。
