# 指標計算モデル 移植仕様書（MQL `OnCalculate` → Python バッチ計算）

MQL インディケーターの**計算部分の概念**を Python へ書き換えるための決定論的仕様。
「誰が実装しても同じ結果になる」水準（曖昧語を排し、契約・場合分け・式・端点を一意に）を
満たすことを目的とする。本書は計算モデル**のみ**を対象とし、アーキテクチャ・描画・テスト・
ディレクトリ規約は `.doc/PORTING_GUIDE.md`（以下「ガイド」）に従う（重複記述はしない）。

- **対象**: `OnCalculate` の計算ロジックを純粋関数へ移す部分。描画 (`#property` / `DRAW_*`)・
  入出力 (`CopyRates` / loader)・色指定は本書の対象外（ガイド §1/§6）。
- **出典**: MQL の計算契約は MetaQuotes 公式リファレンス標準指標
  (`sample/MQL5/Indicators/Examples/`) のコードに基づく。各規則の根拠ファイルを明記する。
- **接地**: Python 側の確立規約（昇順・全件計算・numpy 純粋関数・warm-up）は
  `indicators/profit_rsi/`・`indicators/profit_band/` の実装で実証済み。

---

## 仕様確定事項（9項目・spec-items-clarifier 確定）

本書の位置づけを 9 項目で一意確定する。技術項目（2/5/6/8/9）は本文で決定論的に定義済み。
管理項目（1/3/4/7）は以下に確定する。

| # | 項目 | 確定型 | 内容 |
|---|---|---|---|
| 1 | Objective | **状態到達型** | 全対象 MQL 指標が §4 の 3 アーキタイプいずれかに分類され、各々が純粋関数として実装・存在する状態への到達を成功とする。 |
| 2 | Scope | 確定 | 計算ロジックの純粋関数移植のみ。描画／入出力／色／テスト／ディレクトリ規約はガイドへ委譲（本書冒頭）。 |
| 3 | Assumptions | **出典固定型** | MetaQuotes 公式を計算契約の出典、`profit_rsi`/`profit_band` を実証として所与とする。出典指標の挙動差異が判明した場合は**該当移植を保留し本書を改訂**する。 |
| 4 | Constraints | **精度固定型** | MQL 原挙動との数値一致（端点 NaN/0・シード式・分位点 `method="linear"`・float 精度）を**絶対不変**とし、**検証合格を移植完了の必要条件**とする。日程・移植本数は調整可。 |
| 5 | Input/Trigger | 確定 | 入力＝昇順価格配列 `(o,h,l,c[,v])`。実行＝バッチ全件 1 回の純粋関数（差分実行は不採用、§3）。 |
| 6 | Processing | 確定 | §4 の 3 アーキタイプごとに式・分岐・端点を一意定義。データ不足は §2。 |
| 7 | Entities | **入力配列限定型** | 対象実体は入力 numpy 配列と出力系列のみ。DB／ファイル／状態を持たない純粋関数。 |
| 8 | Output | 確定 | 出力＝`np.ndarray`／不変 DTO。warm-up 値は元コードから一意（NaN/0、§1.3）。 |
| 9 | Exception | 確定 | データ不足＝全 warm-up 系列返却（例外なし、§2）。必須列欠落／不正パラメータ＝例外（ガイド §4／SPEC §8）。 |

> **Item4（精度固定型）の含意**: §6 チェックリストの「検証（手計算可能な小入力で元挙動を固定）」は
> **任意ではなく移植完了ゲート**である。数値一致しない移植は完了とみなさない。

---

## 0. 用語の一意定義

| 記号 | 定義 | 値域 |
|---|---|---|
| `N` | バー総数（MQL の `rates_total`） | 整数 ≥ 0 |
| `i` | バー添字。**昇順（`i=0` が最古、`i=N-1` が最新）** | `0 ≤ i ≤ N-1` |
| `P` | 指標のパラメータ期間（`InpMAPeriod` 等） | 整数 ≥ 1 |
| `warmup` | 値を確定できない先頭区間の長さ | 整数 ≥ 0 |
| `buf[i]` | 指標出力系列の第 `i` 要素 | float または NaN |

> ⚠️ **向きの確定**: MQL の時系列配列は `ArraySetAsSeries(true)` で「`index 0` = 最新足」に
> なりうる。**本書の Python 規約は常に昇順（古→新）**。移植時は元コードの `ArraySetAsSeries`
> の有無を最初に確認し、添字を昇順へ写し替える（ガイド §4-3）。Wilder 平滑・全系列統計の
> ように向きに不変な計算は差が出ないが、`shift`/参照方向を持つ計算は反転すると破綻する。

---

## 1. `OnCalculate` 実行契約（MQL 側の事実）

MQL の `OnCalculate` は端末から**ティック/バー更新ごとに繰り返し**呼ばれ、引数で計算範囲を
受け取る。Python バッチ移植では**この差分実行モデルを採らず、全件を 1 回で計算する**
（ガイド §3 の `rates_total`/`prev_calculated` 行、`profit_rsi` SPEC §2「リアルタイム差分
再計算は対象外＝バッチ全件計算で代替」）。したがって以下は**「元コードを読むための契約」**で
あり、Python へは §3 の変換規則で写す。

### 1.1 引数の意味（一意）
- `rates_total` = 現在の全バー数 `N`。
- `prev_calculated` = **前回の呼び出しが返した値**。初回・履歴再構築時は `0`。
- 戻り値 = 「今回計算済みとみなすバー数」。通常 `return(rates_total)`。次回呼び出しの
  `prev_calculated` になる（状態の引き継ぎ）。

### 1.2 計算起点の決定（実証された普遍規則）
`sample/MQL5/Indicators/Examples/` の全例で、増分計算の起点は**例外なく**:

```
start = (prev_calculated == 0) ? <初期化起点> : prev_calculated - K   (K ∈ {1,2,5,...})
```

- オフセット無し（`= prev_calculated`）の例は **0 件**。
- `K=1` が基本（最新の未確定足を毎回上書きするため）。
- `K≥2` は**先読み／シフト／センタリングを持つ指標**で、まだ確定しない末尾バー数に等しい
  （根拠: `Custom Moving Average.mq5` は `K=1`、Gator 系は `K=2`、`Fractals.mq5` は
  `start=rates_total-5`、`ZigZag.mq5` は直近極値まで最大100本遡る）。

> **Python への含意**: バッチ全件計算では `prev_calculated` は不要になり、`start=0` 相当で
> 全系列を 1 回計算する。ただし **`K` が表す「末尾の未確定バー数」は warmup の末尾版**として
> 残る（§4-3 アーキタイプ3）。

### 1.3 warm-up 区間と空値
- 多くの指標は先頭 `warmup` 本（例 `P-1` 本）を確定できない。MQL は描画開始位置を
  `PlotIndexSetInteger(PLOT_DRAW_BEGIN, warmup)` で指定し、バッファ先頭を `0.0` か
  `EMPTY_VALUE`(=`DBL_MAX`) で埋める。
- **Python での確定規則**:
  - 元が `EMPTY_VALUE` → `np.nan`（描画側で `dropna`。ガイド §4-5）。
  - 元が**リテラル `0.0`** → `0.0`（`profit_rsi` の iRSI warm-up は 0 で確定。元の意図を 1:1 再現）。
  - どちらかは**元コードの代入値で一意に決まる**。推測で NaN/0 を選ばない。

---

## 2. データ不足時の挙動（一意）

MQL は計算不能時 `return(0)`（＝「未計算」のまま次回再試行）を返す例がある
（`MACD.mq5`: `if(rates_total<InpSignalSMA) return(0);`）。ただし**全例が持つわけではない**
（`ColorBars.mq5` は不足ガードを持たない）。Python バッチ移植では:

- `N < warmup+1`（最小必要本数未満）のとき、**全要素を warm-up 値（§1.3）で満たした
  長さ `N` の系列を返す**。例外は投げない（`profit_rsi`: `rates_total<=period` で全 0）。
- 必須列欠落・時刻列欠落・不正パラメータは例外（ガイド §4 / SPEC §8 の異常系に従う）。

---

## 3. 共通変換規則（差分実行 → バッチ全件）

| MQL（読むための契約） | Python（一意な実装） |
|---|---|
| `OnCalculate` 反復呼び出し | 純粋関数 1 回。入力配列 `(o,h,l,c[,v])` → 出力配列。副作用なし。 |
| `prev_calculated` 差分起点 | **使わない**。常に全 `N` 件を計算。 |
| `start = prev_calculated - K` | バッチでは無効化。`K` は末尾 warmup（§4-3）に転化。 |
| `return(rates_total)` | 戻り値は計算済み系列（`np.ndarray` / DTO）。 |
| `double buf[]` / `SetIndexBuffer` | `np.ndarray`（1 バッファ = 1 系列）。 |
| `EMPTY_VALUE` | `np.nan`。 |
| `int(x)` による値の整数化 | **持ち込まない**（float 精度で再実装。ガイド §4-1）。 |
| `MathQuantile(x,p)` | `np.quantile(x, p, method="linear")`（R type-7 一致。ガイド §4-2）。 |
| `IsStopped()` ループ脱出 | 不要（バッチは中断点を持たない）。 |

---

## 4. 3つの計算アーキタイプと決定論的 Python 等価

元 `OnCalculate` の計算戦略は、指標の数学的性質により次の 3 型に分かれる。**各型ごとに
Python バッチ実装が一意に定まる**。移植前に元コードがどの型かを §4-0 で判定する。

### 4-0. 型の判定フロー（一意）
1. `OnCalculate` 内で `CopyBuffer`（他指標ハンドルの取り出し）を使うか
   → **使う：アーキタイプ2**。
2. 使わず、計算起点が `prev_calculated`（§1.2）か
   → **そう：アーキタイプ1**。
3. 計算起点が `rates_total - 定数` または末尾から後方へ遡る走査か
   → **そう：アーキタイプ3**。

---

### 4-1. アーキタイプ1：増分・自前漸化（Incremental self-recurrence）
- **元の形**: `buf[i]` を `buf[i-1]`（および当該足の価格）から漸化式で求める。
  起点 `start = prev_calculated-1`。`prev_calculated==0` で初期値をシード。
- **代表**: `Custom Moving Average.mq5`（SMA/EMA/SMMA/LWMA）, RSI, EMA。
- **不変条件**: 初期シード（`prev_calculated==0` ブロックの初期化）が結果全体を決める。

**Python 等価（一意）**:
```python
def compute(price: np.ndarray, P: int) -> np.ndarray:
    N = price.shape[0]
    buf = np.full(N, WARMUP_VALUE)          # WARMUP_VALUE は §1.3 で元コードから一意確定
    if N < P:                               # §2 データ不足
        return buf
    buf[P-1] = seed(price[:P], P)           # 元の prev_calculated==0 シードを 1:1 で
    for i in range(P, N):                   # 昇順。元の漸化式をそのまま
        buf[i] = recurrence(buf[i-1], price[i], price[i-P], P)
    return buf
```
- `seed` / `recurrence` は**元 MQL の式をそのまま**移す（例 SMA: `buf[i]=buf[i-1]+(price[i]-price[i-P])/P`、EMA: `buf[i]=price[i]*k+buf[i-1]*(1-k)`, `k=2/(1+P)`）。
- ベクトル化してよいが、**漸化（`buf[i-1]` 依存）がある場合は逐次ループで一致を保証**してから
  最適化する。

### 4-2. アーキタイプ2：ハンドル合成（Handle composition）
- **元の形**: `OnInit` で `iMA` 等のハンドルを取得、`OnCalculate` で `CopyBuffer` により
  他指標系列を取り出し、要素ごとに合成する。自己漸化を持たない。
- **代表**: `MACD.mq5`（fast EMA − slow EMA → 信号 SMA）, OsMA, Bears/Bulls Power, Envelopes。
- **不変条件（MQL 固有）**: `BarsCalculated()` で依存指標の計算完了を確認してから使う。

**Python 等価（一意）**:
```python
def compute(price, P_fast, P_slow, P_signal):
    fast = exponential_ma_on_buffer(price, P_fast)   # 共有ライブラリを再利用
    slow = exponential_ma_on_buffer(price, P_slow)
    macd = fast - slow                               # 要素ごと合成（NaN 伝播は元の warmup と一致）
    signal = simple_ma_on_buffer(macd, P_signal)
    return macd, signal
```
- **`BarsCalculated`/`CopyBuffer` は移植不要**（バッチは全系列が常に手元にある）。
- 依存する平滑・標準指標は**`moving_averages` / `mql_builtins` の共有実装を再利用**し、
  パッケージ内で再実装しない（`profit_rsi` core が EMA を共有再利用する方針に同じ）。
- 合成は元の演算子（差・比・SMA 化）を 1:1 で写す。warm-up は被演算系列の NaN/0 を継承。

### 4-3. アーキタイプ3：後方再走査（Trailing re-scan / look-ahead）
- **元の形**: `prev_calculated` を捨て `start = rates_total - 定数` から、または末尾から後方へ
  遡って**既計算領域を再走査**する。新しい足が過去の確定を覆す／未来足を先読みする指標。
- **代表**: `ZigZag.mq5`・`ZigzagColor.mq5`（`i=rates_total-1` から直近極値まで遡及）,
  `Fractals.mq5`（`high[i+1],high[i+2]` 先読みのため末尾再計算）。
- **不変条件**: 「どこまで遡れば確定するか」の判定（極値カウント／先読み本数）。

**Python 等価（一意）**:
```python
def compute(high, low, ...):
    N = high.shape[0]
    buf = np.full(N, np.nan)                 # 先読み/遡及で確定しない端点は NaN
    # バッチでは全系列が見えているので「再走査」は単一の全件パスに退化する
    for i in range(LOOKBACK, N - LOOKAHEAD): # 例 Fractals: LOOKBACK=2, LOOKAHEAD=2
        if pattern_holds(high, low, i):      # 元の不等号・先読みをそのまま
            buf[i] = high[i]                 # or low[i] / 極値処理
    return buf
```
- 差分実行特有の「遡及」は**全件 1 パスに退化する**（バッチは過去全バーを保持するため遡る必要が
  ない）。
- ただし **`LOOKAHEAD`（末尾の未確定バー数）と `LOOKBACK`（先頭 warmup）は元コードの
  添字オフセットで一意に決まり、その区間は NaN**（§1.2 の `K`、§1.3）。
- 極値検出（ZigZag）は**全系列に対する 1 パスのスキャン**として再実装する（元の「直近 N 本
  遡及」は遡及範囲の制限であり、バッチでは全系列対象でよい。ただし元が遡及打ち切り
  （例 ZigZag の `ExtRecalc` で直近 3 極値まで）に依存して結果が変わる場合は、その打ち切り
  規則も 1:1 で再現する）。

---

## 5. アーキタイプ別 一覧

| 観点 | ①増分漸化 | ②ハンドル合成 | ③後方再走査 |
|---|---|---|---|
| 判定 (§4-0) | 起点=`prev_calculated`、`buf[i-1]`参照 | `CopyBuffer` 使用 | 起点=`rates_total-定数`／遡及 |
| 過去値 | `buf[i-1]` を再利用 | 参照しない | 全バー参照（バッチで全件パス） |
| Python 実装核 | 逐次ループ＋シード | 共有関数呼出＋要素合成 | 全件 1 パス＋端点 NaN |
| 移植で消える MQL 要素 | `prev_calculated`分岐 | `BarsCalculated`/`CopyBuffer` | 遡及ループ（全件パスへ退化） |
| 必ず保つ要素 | シード式・漸化式 | 合成演算子・依存期間 | 不等号・先読み/遡及打ち切り |
| 代表 | Custom MA, RSI | MACD, OsMA, Bears | ZigZag, Fractals |

---

## 6. 移植時チェックリスト（計算部分・一意確認）

1. **向き**: `ArraySetAsSeries` を確認し、添字を昇順（古→新）へ写したか（§0）。
2. **型判定**: §4-0 で 1/2/3 のどれかを確定したか。
3. **warm-up 値**: 先頭・末尾の確定不能区間の値が、元コードの代入（`0.0` か `EMPTY_VALUE`）
   から一意に決まっているか（§1.3）。NaN/0 を推測で選んでいないか。
4. **整数化除去**: `int(...)` を float 化したか（ガイド §4-1）。
5. **分位点方式**: `MathQuantile` → `np.quantile(method="linear")`（ガイド §4-2）。
6. **非対称分類**: 同値足など直感に反する分岐を 1:1 で再現したか（ガイド §4-4）。
7. **データ不足**: `N < warmup+1` で全 warm-up 系列を返す（例外を投げない）か（§2）。
8. **依存指標**: アーキタイプ2 で `moving_averages`/`mql_builtins` の共有実装を再利用したか。
9. **検証**: 手計算可能な小入力で元挙動（シード・合成・端点 NaN）を固定したか（ガイド §7）。

---

## 付録: アーキタイプ → 既存移植の対応

| アーキタイプ | 既存 Python 参照 | 計算核 |
|---|---|---|
| ① 増分漸化 | `indicators/profit_rsi/src/core.py`（`compute_rsi`） | Wilder 平滑の逐次漸化＋period シード |
| ② ハンドル合成 | `indicators/profit_rsi/src/core.py`（EMA 平滑） / `*_macd` 系 | 共有 `exponential_ma_on_buffer` 再利用 |
| ③ 後方再走査 | （現時点で該当移植なし。新規移植時は本書 §4-3 に従う） | 全件 1 パス＋端点 NaN |
