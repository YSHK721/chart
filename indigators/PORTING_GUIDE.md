# MQL → Python インディケーター移植ガイド（概念的仕様書）

MetaTrader（MQL4 / MQL5）のインディケーターを Python へ移植する際の**概念モデル・
アーキテクチャ原則・変換規約**を定める。個々の指標実装に先立つ「型」を提供し、
誰が移植しても同じ層構成・同じ品質基準に収束させることを目的とする。

- **対象読者**: `indigators/` 配下にインディケーターを移植・追加する実装者。
- **参照実装**: `indigators/profit_band/`（PRO!fit_Band の移植）。本書の原則はすべて
  この実装で実証済み。各節の「参照」で対応箇所を示す。
- **準拠する上位規約**: プロジェクトのクリーンアーキテクチャ方針・コードスタイル
  （`.serena/memories/design_patterns_and_guidelines.md` /
  `code_style_conventions.md`）に従う。

---

## 1. 基本思想 — 「計算」と「描画/入出力」を分離する

MQL インディケーターは **計算ロジック・データ取得・描画指定が 1 ファイルに混在**する
（`OnCalculate` の中でバッファに値を書き、`#property` で描画を指定する）。移植では
これを**純粋な計算ロジック**と、その周辺の**入出力アダプタ**に分解する。

```
            ┌─────────────── 純粋ロジック（外部I/O非依存・テスト容易）
   MQL      │   core   : 統計・数値計算（numpy のみ）
 1ファイル  │   bands  : 計算結果 → ドメインの成果物（DataFrame 等）
  に混在  ─→│
            │   loader : 入力アダプタ（CSV → OHLC DataFrame）        ┐ 周辺
            │   plot   : 出力アダプタ（matplotlib / PNG）            ├ アダプタ
            └   lwc_chart : 出力アダプタ（lightweight-charts / Line）┘ （差し替え可能）
```

**原則**: 計算は「何を描くか」を知らない。描画・入出力は計算を呼ぶだけで、計算は
描画・入出力を知らない（依存は常に内向き）。これにより描画先（matplotlib /
lightweight-charts / 将来の別 UI）を、計算を一切変更せずに追加できる。

> 参照: profit_band では `src/core.py`（純粋）→ `src/bands.py`（成果物 DataFrame）を
> 中心に、`loader.py` / `plot.py` / `lwc_chart.py` が周辺アダプタとして取り囲む。

---

## 2. アーキテクチャ原則

| 原則 | 内容 |
|---|---|
| 依存方向 | 外側（アダプタ）→ 内側（core）への一方向のみ。core は pandas/lightweight-charts/matplotlib を import しない。 |
| 唯一の数値依存 | core 層に許す外部依存は numpy のみ。成果物層（bands）で pandas を使う。 |
| 境界は Protocol / ダックタイピング | 層境界は `typing.Protocol`（`@runtime_checkable`）で定義。出力アダプタは具体ライブラリ型に依存せず、必要メソッドを持つオブジェクトを引数で受ける。 |
| DTO は不変 | 層間で渡す値は `@dataclass(frozen=True)`。numpy 配列は `__post_init__` で `writeable=False`。 |
| SRP は「アクター」で判定 | 「誰が変更を要求するか」でファイルを分ける（計算式の変更者／入力フォーマットの変更者／描画先の変更者は別アクター）。 |

**出力アダプタは具体ライブラリを import しない**のが重要な含意。例えば lightweight-charts
アダプタは `chart` 引数（`create_line` を持つオブジェクト）をダックタイピングで受け、
`import lightweight_charts` を書かない。これにより指標パッケージの依存を numpy/pandas に
保てる。

> 参照: `src/lwc_chart.py` は `lightweight_charts` を import せず、`add_profit_band(chart, df)`
> の `chart` を duck typing で受ける。テストも Fake チャートで完結する。

---

## 3. MQL → Python 概念対応表

| MQL（MetaTrader） | Python での等価物 | 備考 |
|---|---|---|
| `OnCalculate()` 計算本体 | 純粋関数（`build_*` / `compute_*`） | 副作用なし。入力配列 → 出力配列/DataFrame。 |
| インジケーターバッファ `SetIndexBuffer` / `double buf[]` | DataFrame 列 / `np.ndarray` | バッファ1本＝1系列。 |
| `EMPTY_VALUE`（描画しない値, =DBL_MAX） | `np.nan` | 「点を打たない」は NaN で表現し、描画側で dropna。 |
| `#property indicator_chart_window` | メインチャートに重畳（同一価格スケール） | overlay 型指標。 |
| `#property indicator_separate_window` | `chart.create_subchart(position='below', ...)` | RSI/MACD 等の別ペイン型。 |
| `PlotIndexSetInteger(..., DRAW_LINE)` | `chart.create_line(...)` | 線。 |
| `PlotIndexSetInteger(..., DRAW_FILLING)`（2バッファ間の塗り） | **非対応**。上端/下端の 2 ライン＋（任意で点線）で表現 | wrapper は塗り未公開（§6）。 |
| `STYLE_DOT` / `STYLE_DASH` | `style='dotted'` / `'dashed'`（`LINE_STYLE`） | 線種。 |
| `input` パラメータ | 関数 kwargs または設定用 `frozen dataclass` | 既定値は MQL の `input` 既定に合わせる。 |
| `MathQuantile`（標準ライブラリ） | `np.quantile(x, p, method="linear")` | 同一方式（線形補間 = R type-7）。§4 参照。 |
| `ArraySetAsSeries(arr, true)`（新しい足が index 0） | 既定は**時系列昇順（古い順）**で扱う | 向きの不一致が頻出バグ。§4 参照。 |
| `CopyRates` / `MqlRates`（OHLCTV） | `loader.load_ohlc_csv()` → OHLC DataFrame | データ取得自体（ブローカー接続）はアダプタの責務外。 |
| `rates_total` / `prev_calculated`（差分計算） | バッチは全件再計算、ライブは `Line.update(series)` | §6。 |
| `IndicatorSetString(INDICATOR_SHORTNAME)` / 凡例名 | `create_line(name=...)` | 名前は値列名と一致させる（§5）。 |

---

## 4. 移植上の落とし穴（実証済み・最重要）

1. **`int()` 切り捨ての持ち込み禁止**
   MQL 側が値幅を `int(open - high)` 等で整数化している場合がある。FX 等の小数価格では
   結果が 0 になり破綻する。**float 精度で再実装**し、切り捨ては移植しない（元の意図が
   「丸め」でなく実装都合なら除去する）。
   > 参照: profit_band README「元 MQL5 からの変更点」。

2. **`MathQuantile` ＝ numpy 既定の線形補間**
   MQL 標準の `MathQuantile` は R type-7（線形補間）。`np.quantile(..., method="linear")`
   と一致する。**分位点・パーセンタイル系は numpy 既定でそのまま一致**するが、移植前に
   方式（type-7 か否か）を必ず確認する。

3. **時系列の向き（`ArraySetAsSeries`）**
   MQL の時系列配列は「index 0 = 最新足」。Python（pandas）は通常「先頭 = 最古」。
   ループ条件・`shift`・`rolling` の向きを取り違えると全体が反転する。**本ガイドの規約は
   昇順（古い→新しい）**。MQL コードを読む際は `ArraySetAsSeries` の有無を最初に確認する。

4. **分類・場合分けの非対称性を忠実再現**
   MQL の元ロジックには直感に反する非対称分類（例: 同値足を一部バケットにのみ加算）が
   含まれることがある。**「きれいに直す」前に、まず元の挙動を 1:1 で再現**し、テストで
   固定する。改善は別コミットで根拠を添えて行う。
   > 参照: `collect_distance_samples` の同値足の扱い（pOH/nOL/pHL/nHL へ非対称加算）。

5. **`EMPTY_VALUE` → NaN、描画は dropna**
   描画しない点は MQL では `EMPTY_VALUE`。Python では `np.nan` とし、描画アダプタ側で
   `dropna()` する。0 や前値で埋めない（系列が歪む）。

6. **描画固有の意味論は計算から出さない**
   バッファ番号・プロット色・ウィンドウ番号などは描画の関心事。core/bands に持ち込まず、
   出力アダプタに閉じる。計算層は「数値の意味」（pOL=始値+分位点 等）だけを持つ。

---

## 5. データモデル規約

- **OHLC 入力**: `open` / `high` / `low` / `close` 列を持つ DataFrame。**列名の大小は不問**
  （内部で小文字キーに正規化して参照）。追加列（volume 等）は保持してよい。
- **時刻の解決順序**: 明示指定 > `time` 列 > `date` 列 > `DatetimeIndex`。描画アダプタは
  この順で時刻を取り出す。
  > 参照: `lwc_chart._resolve_times`。
- **成果物 DataFrame**: 列名は機械可読な `{系統}_{パラメータ}` 形式（例 `pOL_99`）。元入力の
  index を引き継ぐ。
- **DTO**: 中間サンプル等は `@dataclass(frozen=True)`。numpy 配列メンバは不変化する。
- **lightweight-charts へ渡す系列**: `time` 列＋**ライン名と完全一致する値列**を持つ
  DataFrame。値列名がラインの `name` と異なると `NameError`。

---

## 6. 出力アダプタ規約

移植物は最低 1 つの出力アダプタを持つ。標準は 2 系統:

- **matplotlib（PNG）**: ヘッドレスで完結（`matplotlib.use("Agg")`）。レビュー用の静止画・
  元 MT5 描画の再現に使う。
- **lightweight-charts（Line）**: インタラクティブ表示。`chart.create_line()` で系列を追加。

### lightweight-charts の制約（必読）
- 公開 API は `create_line` / `create_histogram` のみ。**2 線間の塗り(fill)は未対応**
  （JS には `addAreaSeries` 等があるが Python ラッパー未公開）。塗りバンドは
  **上端/下端の実線＋外側の点線**で表現する。
- 別ペイン指標は `create_subchart(position='below', height=..., sync=True)`。
- 多数ラインを引くときは `price_line=False, price_label=False`（価格軸が埋まるのを防ぐ）。
- ライブ更新は `line.update(series)`（`time`＋値の `pd.Series`）。
- **ヘッドレス実行の前提**（コンテナ等）: 有効ロケール（`LANG/LC_ALL=en_US.UTF-8`）と
  WebKit 回避 env が必須。これを欠くと `toLocaleString` が `RangeError` で停止する。
  ```bash
  LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
  WEBKIT_DISABLE_COMPOSITING_MODE=1 WEBKIT_DISABLE_DMABUF_RENDERER=1 \
  LIBGL_ALWAYS_SOFTWARE=1 WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1 \
  xvfb-run -a python <script>
  ```
  > 参照: `.doc/lwc_verify/`（検証記録）、profit_band `lwc_demo.py`。

### アダプタ設計の不変条件
- 具体ライブラリを import しない（duck typing）。指標パッケージの依存を増やさない。
- 計算は `build_*` に委譲し、アダプタは「取り出し→マッピング→描画」のみ。

---

## 7. テスト規約

| 観点 | 方針 |
|---|---|
| 元 MQL との整合 | 分類規則・分位点方式・符号（±）を、小さな手計算可能な入力で固定。 |
| 解析解 / 参照値 | 既知の解（あれば）と数値比較（`np.allclose`）。 |
| 出力アダプタ | **Fake オブジェクト**（`create_line` を持つスタブ）で検証し、描画ライブラリに依存させない。本数・スタイル・値・異常系を確認。 |
| 異常系 | 必須列欠落・時刻列欠落・空バケット・未知パラメータで適切な例外。 |
| import 規約 | `sys.path.insert(0, parents[1])` → `from src import ...`（既存テストに準拠）。 |

> 参照: `tests/test_profit_band.py`（計算）/ `tests/test_lwc_chart.py`（Fake チャート）。

---

## 8. 標準ディレクトリ構成

```
indigators/<indicator_name>/
├── README.md            # 概要・構成表・使い方・元 MQL からの変更点
├── src/
│   ├── __init__.py      # 公開 API（build_*, load_*, 中間計算）
│   ├── core.py          # 純粋計算（numpy のみ）
│   ├── <result>.py      # 計算結果 → 成果物 DataFrame（例: bands.py）
│   ├── loader.py        # 入力アダプタ（CSV → OHLC DataFrame）
│   ├── plot.py          # 出力アダプタ（matplotlib / PNG）
│   └── lwc_chart.py     # 出力アダプタ（lightweight-charts / Line）
├── tests/
│   ├── test_<core>.py   # 計算の検証
│   └── test_lwc_chart.py# 出力アダプタの検証（Fake）
└── lwc_demo.py          # デモ（HTML / スクリーンショット生成）
```

各 `.py` 冒頭の docstring に、①層名/責務 ②含む構造 ③元 MQL の対応箇所 ④依存
（標準/外部/プロジェクト内）を記す。`from __future__ import annotations` を先頭に置く。

---

## 9. 移植手順（チェックリスト）

1. **元 MQL 解読**: `OnCalculate` の入出力、バッファ本数、`#property`（chart/separate, DRAW_*）、
   `input`、時系列の向き（`ArraySetAsSeries`）、使用する標準関数（`MathQuantile` 等）を把握。
2. **概念対応付け**: §3 の表で各要素を Python 等価へ写像。描画関心と計算関心を仕分け。
3. **落とし穴チェック**: §4 の各項（int 切り捨て／分位点方式／向き／非対称分類／EMPTY_VALUE）を確認。
4. **core 実装**: 純粋関数として計算を実装（numpy のみ）。中間 DTO は frozen。
5. **成果物層実装**: 計算結果を `{系統}_{パラメータ}` 列の DataFrame に整形。
6. **入力アダプタ**: `loader.py`（必須列検証・時刻列の扱い）。
7. **テスト（計算）**: 元挙動の 1:1 再現を固定。異常系も。
8. **出力アダプタ**: `plot.py`（matplotlib）/ `lwc_chart.py`（duck typing）。fill 非対応の制約を踏まえる。
9. **テスト（アダプタ）**: Fake チャートで本数・スタイル・値・異常系。
10. **デモ＋目視**: `lwc_demo.py` で HTML/スクショ生成、Xvfb＋有効ロケールで実描画確認。
11. **README ＋ 仕様書記入**: 構成表・使い方・元 MQL からの変更点。下記テンプレートを記入。

---

## 10. 移植仕様書テンプレート（各インディケーターで記入）

> 新規移植時は `indigators/<name>/SPEC.md` として以下を埋める。
> 「誰が実装しても同じ結果になる」水準（曖昧語を排し、計算式・場合分けを一意に）を満たすこと。

```markdown
# <Indicator Name> 移植仕様書

## 1. Objective（目的）
- この指標が表す概念 / 何を可視化・算出するか。

## 2. Scope（範囲・対象外）
- 移植する: 計算 / 描画（overlay or separate）/ 入力。
- 対象外: （例）ブローカー接続、アラート、最適化入力 等。

## 3. 元 MQL 情報
- ファイル / バージョン / MQL4|MQL5。
- バッファ本数・プロット数、chart_window / separate_window。
- input パラメータ一覧（名前・型・既定値・意味）。
- 時系列の向き（ArraySetAsSeries: true/false）。
- 使用する標準関数（MathQuantile 等）と方式。

## 4. Input（入力）
- 必須列（open/high/low/close ほか）・時刻列・前提。

## 5. Processing（計算定義）— 一意に
- 各場合分け（陽線/陰線/同値 等）の規則。
- 分位点/平均/丸めの方式（type, 補間, float/int）。
- バンド/系列の定義（例: pOL = open + quantile）。
- 落とし穴対応（§4 のどれに該当し、どう扱ったか）。

## 6. Entities / 成果物（出力データ）
- 成果物 DataFrame の列（`{系統}_{パラメータ}`）と意味。
- EMPTY_VALUE → NaN の扱い。

## 7. Output（描画）
- overlay / separate。
- 系統 → 線種・色・不透明度の対応（fill は上下ライン表現）。
- price_line/label、凡例方針。

## 8. Exception（異常系）
- 必須列欠落 / 空バケット / 時刻列欠落 / 不正パラメータ時の挙動（例外種別）。

## 9. 元 MQL からの差分
- 意図的に変えた点（例: int 切り捨て廃止）と根拠。
- 元と一致を保証する点（分類・符号・分位点方式）。
```

---

## 付録: profit_band 参照対応

| 概念 | 元 MQL5（PRO!fit_Band） | 移植先 |
|---|---|---|
| 計算本体 | `OnCalculate` 内の値幅集計・分位点 | `src/core.py`（`collect_distance_samples` / `compute_quantiles`） |
| バッファ書き込み（始値±分位点） | `UpdateBuffer` | `src/bands.py`（`build_bands`） |
| 描画（塗り＋点線） | `DRAW_FILLING` + `STYLE_DOT` バッファ | `src/plot.py` / `src/lwc_chart.py`（上下実線＋外側点線） |
| 値幅の整数化 | `int(...)` | **廃止**（float 精度） |
| 分位点 | `MathQuantile` | `np.quantile(method="linear")` |
