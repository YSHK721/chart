# ma_marod 基本設計書（新指標: 移動平均乖離率オシレータ・MA 種別選択式）

- 作成日: 2026-07-21
- ステータス: 実装済み（2026-07-21 承認→実装・全テスト通過・実 UI ライブ/リプレイ検証済み）
- 対称参照: btlm_trail_marod（乖離率別 pane オシレータの前例）／moving_averages（基準線 MA の参照実装）
- 基準線の裁定: **ma_type 選択式（sma/ema/smma/lwma）・既定 ema**（ユーザー裁定 2026-07-21。
  moving_averages 指標の既定と対称）

## 1. 目的・背景（実測根拠と適用範囲）

価格の移動平均（種別選択式）からの乖離率を、別 pane のオシレータとして可視化する。

実測根拠（2026-07-20・実 JP225 日足 2012–2026・スクリプト scratchpad/marod_tradability.py 系）:
- 同一ルール比較(下側 5% 分位バンド割れ→翌寄買い・5 日保有・往復 2bp)で、**sma 基準**の乖離率は
  OLS 基準の MAROD を全窓長（50–500）・前後半の両方で上回った
  （最良 length=50: net +181.6%・+60bp/trade・Sharpe 1.83・maxDD −7.1%・シフト帰無 p=0.0003）。
- **上記実測は ma_type=sma の場合のみに適用される。既定の ema を含む他種別の乖離率は未実測**
  （＝既定 ema は表示既定であり、有意性・取引可能性の主張を伴わない）。
- btlm_trail_marod（OLS 基準）は「OLS トレンドとの相対位置」の計器として存続し、
  ma_marod は「MA との相対位置（押し目/過熱の深さ）」の計器として**並立**する（置換ではない）。

## 2. 定義（数式・確定仕様）

```
MA_MAROD_t = (price_t − ma_t) / ma_t × 100        [%]
price      = 単一の解決済みソース配列（8 択・既定 close。解決は moving_averages と同期＝§2.1）
ma_t       = MA(price, ma_type, length)[t]        （moving_averages 参照実装に一致）
```

### 2.1 ソース解決（計算の原子＝MA と同期・単一経路）

- `source` 値の解決は **moving_averages と同一の写像**
  （`moving_averages/src/lwc_chart.py: _SOURCE_TO_APPLIED` → 共有 `common.applied_price` 委譲）
  に一致させる。btlm_trail 経由の解決は用いない。
- 解決は **1 回だけ**行い、得られた同一の price 配列を分子（price_t）と MA 入力の両方に供給する
  （分子と基準線でソースが乖離する余地を構造的に排除）。
- 写像の同値性は moving_averages の写像テーブルとの同一性テストで恒久固定する（§8）。

- MA は参照実装 `moving_averages/src/core.py` の 4 公開関数に**絶対一致**させる:
  `simple_ma_on_buffer`（sma）／`exponential_ma_on_buffer`（ema）／
  `smoothed_ma_on_buffer`（smma）／`linear_weighted_ma_on_buffer`（lwma）。
  種別→関数と有効開始の写像は `moving_averages/src/lwc_chart.py: _main_ma` の規約に一致させる
  （ema は先頭から有効＝`_FROM_ZERO`、sma/smma/lwma は先頭 length−1 本 NaN、`length ≥ 2`）。
  一致は同入力比較テストで恒久固定する（§8）。
- 0 除算（ma_t = 0）は errstate で抑制し、生じた inf/NaN は NaN に落として描画から除外
  （btlm_trail_marod core と同一規約）。
- バンド（別 pane 内・オシレータに重畳）:
  - 経験分位バンド: `btlm_trail_marod.marod_quantile_bands(series, window_n, q_low, q_high)`
    を**無改変参照**（系列汎用関数・当該バー除外の因果窓・有限本数 < 2 は NaN）。
  - σ バンド: 同 `marod_sigma_band(series, window_n, mult)` を無改変参照
    （MAROD 実測で分散非定常＝固定バンド不可のため、ma_marod もローリングのみを提供し
    固定バンドは設けない）。
    **描画廃止（2026-07-21 ユーザー裁定）**: σ バンドは経験分位バンドと実質重複し認知負荷
    が大きいため描画対象から除外。core の計算関数（`ma_marod_sigma_band`）は温存（復帰容易）。
  - 外れ値イベント分位の水準線（2026-07-21 仕様確定・ユーザー裁定）: 正常バンド
    （q_low/q_high）を超えた MA_MAROD 値を「外れ値イベント」とし、そのイベント値集合に
    対する因果分位を水準線として描く（`ma_marod_evq_{med|ext}_{hi|lo}`・描画は直近 K 件の
    4 本。2026-07-21 裁定で _all の描画は廃止）。
    トレード時に「外れたら典型的に／極端にどこまで行くか」を**事前に把握する**ための水準。
    - 水準（上側・下側それぞれ）: 中央値＝典型深度（実線）／極端分位＝q_out 流用・上側
      q_out・下側 1-q_out（破線）。集計は直近 k_events 件（濃い赤・分散非定常対策＝実測
      2026-07-20 ローリング必須）。
      **全履歴（_all）系列の描画は廃止（2026-07-21 ユーザー裁定・認知負荷削減）**: core は
      _all キーを計算し続けるが表示層は emit しない（復帰容易）。
    - 集計単位 `event_agg`（2026-07-21 追補・ユーザー裁定）: **episode（既定）**＝連続超過
      バーの 1 まとまりを 1 エピソードとし、その極値（上側 max・下側 min）を 1 観測とする
      runs declustering。バー単位は持続時間（平均 4〜5 本/回・実測）の重み付けで典型深度を
      歪め（上側バー中央値 +24.6% vs エピソード極値中央値 +19.1%）、直近 50 バー＝実質
      10〜11 エピソードと独立標本数を約 5 倍過大評価する実測に基づく統計的改善。
      **bar**＝バー値（旧方式）も選択式で保持し、確認の結果次第で UI から即復帰できる。
      エピソードは「超過が途切れたバー」で確定し次バー以降の水準に反映（因果）。データ
      末尾の進行中エピソードは未確定のため集計に含めない（非リペイント）。
    - 因果・非リペイント: イベント判定は当該バー除外の因果バンド、バー t の水準は t より
      前のイベントのみから計算。現在バーの水準は足が動く前から確定＝事前把握可能。
    - 設計判断の経緯（いずれも実測・実 UI/実 HTTP 経路 2026-07-21）:
      ① バンド線（off_hi/lo）案 → 因果分位バンドは新記録スパイクを原理的に含められない
      （0.99 で 4.67%・理論限界 rolling min/max でも 1.44% が外に残る実測）ため棄却。
      ② 超過点の赤ドット案 → 事後マークであり「事前に水準を把握して対応する」トレード
      要件を満たさずユーザー裁定で棄却。
      ③ イベント分位（本仕様・採用）: 実測で通常分位と異なる新情報（例: 直近時点の上側
      イベント中央値 +24.6% は通常バンド上端 +18.7% のさらに先）。全履歴のみの集計は
      古いレジームに引きずられる実測（下側全履歴中央値 −16.1% < 現行バンド下端 −23.0%
      の浅さ）を確認し、直近 K 件ローリングを主・全履歴を参照として併記。
    - 極端分位の有効条件 `max(q_high, 0.5) < q_out < 1`（q_out ≤ q_high は正常バンド以浅、
      q_out ≤ 0.5 は「極端」の意味を失う）。無効・空欄は**極端線のみ黙って無効化**
      （中央値線は常時・btlm_trail q_out の前例規約）。イベント数 < 5 は NaN（描画除外）。

## 3. 因果・非リペイント（成立機構）

- ma_t は df[:t+1] のみに依存＝確定バーの値は後続データ追加で不変（全 4 種別）。
- ema/smma は再帰形で系列先頭（ロード起点）への依存を持つが、これは moving_averages 指標が
  製品で既に持つ性質と同一（同一ロード規約下で決定論的）。新たな性質は導入しない。
- バンドは当該バー t を除く直近 window_n 本（ISSUE-141 と同一の因果境界）。
- ライブ・リプレイ（untilTime=to の単一時計・df[:t+1] 再計算）で同一値が成立する。
  リプレイ側はライブ挙動への厳密一致を最優先とする（既定方針）。

## 4. パッケージ構成（OCP・主機能無改変の参照拡張）

```
indigators/ma_marod/
├── conftest.py
├── src/
│   ├── __init__.py        # 公開 API 再エクスポート（btlm_trail_marod と対称）
│   ├── core.py            # 純粋ロジック（numpy のみ・外部 I/O 非依存）
│   └── lwc_chart.py       # 別 pane 描画（line/histogram・バンド）
└── tests/
    ├── test_ma_marod.py
    └── test_lwc_chart.py
```

参照機構（すべて read-only・無改変。前例＝btlm_trail_marod `_load_btlm_trail` の
一意名ファイルロード方式をそのまま踏襲）:

| 参照先 | 用途 | ロード名（一意） |
|---|---|---|
| `btlm_trail_marod/src` | `marod_quantile_bands` / `marod_sigma_band` / `marod_outlier_event_quantiles`（系列汎用） | `_btlm_trail_marod_src_for_ma_marod` |
| `moving_averages/src` | 4 種 `*_on_buffer`（MA 参照実装）＋ソース写像の同期元（§2.1） | `_moving_averages_src_for_ma_marod` |
| `common/applied_price` | 合成価格の実体（moving_averages と同一の委譲先・絶対 import） | —（通常 import） |
| `common/event_quantiles` | 外れ値イベント分位の計算の正＋表示規約ヘルパー（2026-07-21 共有化・ユーザー裁定。lwc_chart が `emit_event_quantile_lines` を直接利用） | —（通常 import） |

- 共有化の構成（2026-07-21 裁定・指標横断展開の基盤）: 計算の正と表示規約（系列名
  `{prefix}_evq_*`・色・線種）は `common/event_quantiles.py`、UI 定義（3 パラメータ＋
  SeriesDef 4 本）は catalog.js の共有ビルダー `EVQ_PARAMS` / `EVQ_SERIES_DEFS`。
  btlm_trail_marod core は系列レベル API（バンド算出＋委譲）を公開し ma_marod はそれを参照。
  他指標への展開は「lwc_chart で 2 呼び出し＋catalog で 2 spread＋schema 3 既定値」で完結する。
  アクター分離は不採用（独自データ・ライフサイクルを持たない純関数のため過剰・裁定 2026-07-21）。

- 既存パッケージ（btlm_trail_marod / moving_averages / common）への変更は一切行わない
  （共有リソースの破壊的変更禁止）。btlm_trail への依存は持たない（ソース解決を MA と同期したため不要）。
- 種別→関数・有効開始の写像テーブルは ma_marod 側に保持するが、その挙動は参照実装
  `_main_ma`（私有関数のため直接依存しない）と同入力比較テストで一致固定する（§8）。
- core 公開 API（btlm_trail_marod と対称）:
  - `ma_marod_series(df, *, source="close", ma_type="ema", length=50) -> np.ndarray`
  - バンドは btlm_trail_marod の系列汎用関数をそのまま利用（再実装しない）。

## 5. パラメータ（既定値・範囲・根拠）

| 名前 | 既定 | 範囲 | 根拠 |
|---|---|---|---|
| `source` | `close` | 8 択 | 解決は moving_averages と同期（§2.1）。enum 値・ラベルも moving_averages（`MA_SOURCE_LABELS`）と同一 |
| `ma_type` | `ema` | sma/ema/smma/lwma | ユーザー裁定（moving_averages 既定と対称）。実測根拠は sma のみ（§1） |
| `length` | 50 | ≥ 2 | sma 実測最良（2026-07-20・50–200 で頑健）。ema 等での最適値は未実測。下限は参照実装の契約 |
| `window_n` | 500 | ≥ 2 | btlm_trail_marod DEFAULT_WINDOW_N と対称（実測もこの値で実施） |
| `q_low` / `q_high` | 0.05 / 0.95 | 0<low<high<1 | 同上（実測ルールと同値・カタログ公開＝marod と対称） |
| `q_out` | 0.99 | max(q_high, 0.5)<q_out<1（範囲外・空欄は極端線のみ黙ってオフ） | 外れ値イベントの極端分位（上側 q_out・下側 1-q_out・破線）。有効条件の規約は btlm_trail q_out に対称。既定 0.99 はユーザー裁定 2026-07-21 |
| `k_events` | 50 | ≥ 1 | イベント分位ローリングの直近観測件数（episode ではエピソード数。分散非定常の実測 2026-07-20 によりローリング主・全履歴は参照併記。ユーザー裁定 2026-07-21） |
| `event_agg` | `episode` | episode / bar | イベント集計単位。episode＝エピソード極値（declustering・統計的改善の実測根拠あり）／bar＝バー値（旧方式・復帰用に保持）。ユーザー裁定 2026-07-21 |
| σ 倍率 | 2.0（core 定数） | — | btlm_trail_marod SIGMA_MULT と対称（カタログ非公開＝marod と同一扱い） |
| `color` | `rgba(255, 152, 0, 1)`（仮） | — | marod（紫系）と識別可能な橙系。実 UI 確認時に最終決定 |

- パラメータ名は基準線の定義元である moving_averages と同名（`ma_type`/`length`/`source`）とし、
  UI 上の一貫性を優先する（btlm_trail_marod の `maxbars` とは意図的に別名＝基準線が異なるため）。

## 6. UI 統合（catalog / binding / web）

btlm_trail_marod のライブ検証済み経路と対称に、以下へ **追記のみ**（既存エントリ無改変）:

1. `indicator_ui/api/adapter/compute/catalog_schema.py` — `"ma_marod"` 既定値（§5）。
2. `indicator_ui/api/adapter/compute/call_binding.py` — loader 登録（`ma_marod` → lwc_chart 公開関数）。
3. `indicator_ui/web/js/usecase/catalog.js` — カタログ表示名・`ma_type` ENUM
   （moving_averages の `MA_TYPE_LABELS` と同ラベル）・`source` ENUM
   （moving_averages の `MA_SOURCE_LABELS` と同ラベル＝§2.1 の同期を UI 側でも維持）・スタイルタブ定義
   （線種 4 択 dot/solid/dotted/dashed ＋ histogram 切替＝marod のスタイルタブ仕様と同一）。
4. 表示: 別 pane オシレータ（0% 基準線・正常バンド・イベント分位水準線重畳）。
   イベント分位系列は静的名 `ma_marod_evq_{med|ext}_{hi|lo}` の 4 本・
   `rgba(210, 67, 58, 1)`（赤系・btlm_trail `_COLOR_OFFSET` と同系）。中央値＝実線・
   極端分位＝破線。σ バンド・全履歴（_all）系列は描画廃止（2026-07-21 ユーザー裁定・
   認知負荷削減。core 計算は温存）。表示層変更なし。
5. golden: `api/tests/golden/catalog_defaults.json` 再生成（追記差分のみ）。

## 7. リプレイ統合

- `simulator/replay_ui/web/js/adapter/front/replay_indicator_controller.js` の
  `INTRABAR_FORMING_IDS` に `'ma_marod'` を**追加登録**する（必須）。
  未登録だと足内 tick 更新で確定値ジャンプになる（ISSUE-145 の確定規約）。
  末尾点のみ forming で動き、バンドは当該バー除外の因果窓ゆえ据え置き
  （btlm_trail_marod 登録時と同一パターン・実証済み）。
- untilTime/forming の素通し機構は共通実装のため**変更不要**。

## 8. テスト計画

| 層 | 内容 |
|---|---|
| core 単体 | ①4 種 MA が参照実装（moving_averages `_main_ma` 経路）と同入力で数値一致 ②有効開始規約（ema は先頭から・他は length−1 本 NaN） ③因果性（末尾にデータ追加しても既存バー値不変・全 4 種） ④0 除算→NaN ⑤length<2 / 未知 ma_type / 未知 source で ValueError ⑥ソース写像が moving_averages の写像テーブルと同一（同期固定・§2.1）⑦分子と MA 入力が同一配列（単一経路）であることの検証 |
| lwc_chart | btlm_trail_marod test_lwc_chart と対称（系列長・NaN 除外・スタイル切替） |
| binding | test_ma_marod_binding（marod の binding テストと対称）＋ golden 差分 |
| web | catalog.test.js へのカタログ項目テスト追記（ma_type ENUM 含む） |
| 実 UI | ライブ・リプレイ両方で実ブラウザ確認（compute 直叩き・合成データは検証根拠にしない） |

## 9. スコープ外（本設計に含まない）

- 売買シグナル生成・バックテスト機能（検証は scratchpad で実施済み・製品機能ではない）。
- btlm_trail / btlm_trail_marod / moving_averages の変更（読み取り専用参照のみ）。
- 固定（非ローリング）バンド（MAROD 分散非定常の実測により不採用）。
- ema/smma/lwma 基準の乖離率の有意性・取引可能性検証（必要なら別タスクで実測）。

## 10. 未検証事項（明示）

- **ema（既定）・smma・lwma 基準の乖離率の統計的性質は未実測**。実測済みは sma・length=50 の
  組合せのみ（§1）。既定 ema はユーザー裁定による表示既定であり、性能主張を伴わない。
- OLS 屈曲 vs SMA 遅行という機構説明は推論であり未検証。設計判断には使用していない。
- 既定色・スタイル既定値は実 UI 確認で最終決定。
- 取引運用上の実コスト（急落翌寄のスプレッド実測）は未検証（指標本体の設計には影響しない）。
