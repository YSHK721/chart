# Latest 増分計算 内部設計書（ISSUE-233 抜本的解決）

## 1. 文書情報

- 作成日：2026-08-01
- バージョン：v0.1.0（内部設計・実装は別承認）
- 上位文書：`.doc/indicator-management-ui/内部設計書.md`
- 対象 Issue：ISSUE-233（リプレイ再生が遅い／指標の足内更新粒度が指標の重さで劣化する）
- 背景：ISSUE-145（2026-07-20）・ISSUE-232（2026-08-01）はいずれも本設計の真因に触れておらず、症状が再発している。応急処置（予算制）はユーザー厳命により撤回済み。

## 2. 用語

| 用語 | 意味 |
|---|---|
| **バー送り** | 確定した足から次の足へ進むこと |
| **足内更新** | 1 本の足ができていく過程を、その足の中で刻んで見せる機能 |
| **更新粒度** | 足内更新を 1 本あたり何回行うか |
| **full 計算** | `adapter.compute` を全件で呼び、全バーの系列を返す（`latest_dispatch.full_compute`） |
| **latest 計算** | 末尾 K 点だけを返す計算（`latest_dispatch.latest_compute`）。**現状は full 計算の切り出しであり増分ではない** |
| **依存深度** | 末尾 1 点の値が確定するために必要な、末尾から数えた最小バー本数 |
| **増分計算** | 前バーまでの状態を保持し、1 点だけ進める計算。所要は依存深度に依らず一定 |
| **形成中バー** | 確定前の最新足。足内更新のたびに OHLC が変わる |

## 3. 真因（実測・コード確認で確定）

### 3.1 latest 計算は増分ではない

`indigators/indicator_ui/api/adapter/compute/latest_dispatch.py:56-58`

```python
meta = latest_meta(compute_id, variant, params)
sub = df if meta.min_window is None else df.tail(meta.min_window)
series = adapter.compute(compute_id, variant, sub, params)
```

末尾 1 点（`trailing_k=1`）を得るために `adapter.compute` を呼び、全バーぶん計算してから末尾を切っている。`min_window` が `None` のとき tail すらせず全件で計算する。

### 3.2 23 指標中 21 指標が `min_window=None`（全件）

`latest_meta` を宣言しているのは `call_binding.py` の 2 件のみ（`price_range_power`:419 / `moving_averages`:434）。未宣言の指標は `latest_meta.py:54` の安全既定 `LatestMeta("recurrence", None, 1)` に落ちる。

`latest_meta.py` の冒頭は本フレームワークを「後続 21 指標が従う基盤」と記しており、**基盤（Stage A）だけが実装され、指標ごとの宣言が未着手のまま**である。これが真因である。

### 3.3 実測（2026-08-01・実 HTTP 経路・1h・窓 1386 本）

| 指標 | latest 1 回の所要 | full 1 回の所要 |
|---|---|---|
| `btlm_trail`（`band_method=empirical`・`empirical_n=495`・`n_cov=495`） | 334ms | 390ms |
| `ma_marod`（`window_n=495`） | 159ms | 210ms |
| `moving_averages`（`ema`・`length=24`） | 8〜105ms | 50ms |

窓ロード（固定費）は 0.05 秒に過ぎず、支配しているのはこの全再計算である。**latest が full とほぼ同じコストであることが、本設計が解く問題そのもの**である。

### 3.4 帰結：更新粒度が指標の重さで決まる

足内更新 1 回に往復 1 回が要り、その所要は上表で決まる。1 足の長さ（1h・1分OHLC・201 点・6ms/点）は 1.21 秒であるため、更新粒度の上限は「1.21 秒 ÷ 1 往復の所要」になる。実測は以下のとおりで、理論値と一致する。

| 構成 | 指標更新/足 | ローソク更新/足 |
|---|---|---|
| `moving_averages` のみ | 27.2 回 | 202 |
| ＋ `btlm_trail`（経験分位） | 4.0 回 | 201 |
| ＋ `ma_marod`（3 指標） | 0 回 | 201 |

指標を足せば必ず落ちる。ISSUE-145・ISSUE-232 のどちらもこの構造に触れていない。

---

## 4. 参照実装の確認（着手前必須・CLAUDE.md）

**新しい計算方法を発明しない。** 参照実装（各指標の `src`）が既に増分計算の入口を持つ。本設計はそれを使うだけである。`src` は read-only（`indicator_compute_adapter.py` 冒頭の規約）であり、**一切改変しない**。

### 4.1 `moving_averages`：MQL 由来の `prev_calculated` 契約が既にある

`indigators/moving_averages/src/core.py:190-228`

```python
def exponential_ma_on_buffer(rates_total, prev_calculated, begin, period, price, buffer) -> int:
    ...
    if prev_calculated == 0:      # 初回計算 または 本数変化
        ...
        start_position = period + begin
    else:
        start_position = prev_calculated - 1
    for i in range(start_position, rates_total):
        buffer[i] = price[i] * smooth_factor + buffer[i - 1] * (1.0 - smooth_factor)
    return rates_total
```

`prev_calculated`（前回計算済み本数）と `buffer`（＝保持する状態）で、**続きから計算する契約が実装済み**である。`sma` / `smma` / `lwma` も同じシグネチャを持つ（同ファイル 147 / 340 / 231 行）。

ところが薄いラッパ `core.ma()`（同 411-439 行）が

```python
fn(n, 0, 0, length, values, buffer)   # prev_calculated=0 固定
```

と常に 0 を渡すため、**増分契約が使われていない**。`ma_marod/src/core.py:86-96` も同じ `*_on_buffer` 契約を要求している。

### 4.2 `btlm_trail`：バー間の再帰が無い（窓独立）

`indigators/btlm_trail/src/core.py:112-119`

```python
for t in range(n):
    w = min(maxbars, t + 1)
    z = prices[t - w + 1: t + 1]
    m, ps, b1, s = _window_end_scalar(z)
```

各バーは直近 `maxbars` 本のみに依存し、バー間の再帰は無い。

**ただし単窓関数 `_window_end_scalar`（同 68 行）と経験分位 `_empirical_quantile_causal`（`trail.py:71`）は非公開**である（先頭アンダースコア。`btlm_trail/src/__init__.py` の `__all__` に無い）。公開されているのは `rolling_ols_window_end` / `rolling_coverage` / `realized_coverage_latest` / `build_btlm_trail` / `resolve_source` / `norm_ppf` と既定値のみ。この制約が §5.5 の判断を生む。

被覆率は `trail.py:198` の `realized_coverage_latest`（**最新確定バー専用の入口が既にある**・公開済み）。

### 4.3 依存深度（実装から導出）

| 指標 / 系列 | 依存深度 |
|---|---|
| `moving_averages`（`ema` / `smma`） | 先頭からの再帰＝**無限**（増分でしか解けない） |
| `moving_averages`（`sma` / `lwma`） | `length` 本（ただし core はスライド和の再帰・§5.2 参照） |
| `btlm_trail` の mean / beta / sigma | `maxbars` 本 |
| `btlm_trail` の経験分位バンド | `empirical_n + maxbars` 本 |
| `btlm_trail` の `band_hit_rate` | `n_cov + empirical_n + maxbars` 本 |

---

## 5. 設計

### 5.1 方針

**latest 計算を「full の切り出し」から「保持した状態を 1 点進める」へ変える。** 具体的には `LatestMeta` に第 4 の archetype `incremental` を加え、指標ごとに **状態の型・初期化・1 点前進** を宣言する。

宣言が無い指標は現行の安全既定（full＋K=1）のままで、**挙動は 1 ビットも変わらない**（OCP：既存経路は不変、宣言した指標だけが新経路へ乗る）。

### 5.2 段階分割（この順に実施する）

| 段階 | 対象 | 内容 | 期待効果 |
|---|---|---|---|
| **S1** | `moving_averages`（4 種）・`ma_marod` の基準線 | `*_on_buffer` の `prev_calculated` 契約を使う増分器（§5.3） | 1 往復 8〜105ms → 1ms 未満 |
| **S2** | `btlm_trail` の mean / beta / sigma / pred_sd | `_window_end_scalar` を末尾 1 窓だけに適用 | 窓 1386 → `maxbars`(115) |
| **S3** | `btlm_trail` の経験分位バンド | 順序統計構造（挿入・削除・分位取得）で `_empirical_quantile_causal` を増分化 | 窓 610 → O(log n) |
| **S4** | `btlm_trail` の `band_hit_rate` | `realized_coverage_latest`（既存入口）＋リング統計 | 窓 1105 → O(1) |
| **S5** | `ma_marod` / `btlm_trail_marod` のイベント分位（`k_events`・エピソード declustering） | エピソード状態機械 | 未測定（S1〜S4 後に再測定して判断） |

**S1 で止めても効果が出る**（`moving_averages` は 3 インスタンス適用が実測構成）。段階ごとに独立してリリース可能とする。

`sma` / `lwma` は core がスライド和の再帰であり、`latest_meta.py` 冒頭が記すとおり開始点を変えると末尾値に浮動小数ドリフト（実測 ~1e-15）が乗る。したがって **tail による短縮は採らず、S1 の増分器（`prev_calculated` 契約＝ドリフトが原理的に生じない）で解く**。

### 5.3 状態の保持

#### 5.3.1 状態の定義

```
IncrementalState = {
    key:        (compute_id, variant, params_hash, dataset_ref, timeframe)
    confirmed_time: int          # 状態が対応する最後の確定バーの time
    payload:    Any              # 指標ごとの状態本体（buffer / リング / 順序統計構造）
}
```

#### 5.3.2 不変条件（最重要）

**1 点前進は純関数とする。**

```
step(state, bar) -> series_tail            # state は変更しない
advance(state, bar) -> state'              # 確定時のみ状態を進める
```

足内更新は「同じ確定状態から、形成中バーを差し替えて何度でも呼ぶ」操作である。`step` が状態を破壊すると 2 回目以降の値が壊れる。したがって **`step` は state を読むだけ**とし、状態の前進は確定バー到達時の `advance` でのみ行う。

これにより足内の N 回の更新が、いずれも「確定状態 ＋ 形成中バー 1 本」から O(1) で得られる。

#### 5.3.3 置き場所

サーバ側（`serve_replay` の compute worker 内）に **キー付き LRU キャッシュ 1 個**を置く。

- ヒット条件：`key` 一致 かつ `confirmed_time` が要求 `untilTime` の直前確定バーと一致
- ミス時：`init_state(df.tail(依存深度))` で構築（初回のみ full 相当のコスト）
- 無効化：`params` / `timeframe` / `datasetRef` のいずれか変化で別キー＝自然に無効化
- 上限：インスタンス数 × 2 程度（実測構成で 7 指標＝14 エントリ）。超過は LRU で破棄し再構築

**キャッシュは最適化ではなく仕様である**（状態を持たなければ増分計算は成立しない）。ヒットしなくても値は full と同一であり、遅くなるだけで壊れない。

### 5.5 btlm_trail は追加公開が必要（B-2）

`btlm_trail` の増分化に要る 2 関数（`_window_end_scalar` / `_empirical_quantile_causal`）は非公開である（§4.2）。取りうる選択は 2 つしかない。

| 案 | 内容 | 到達点 |
|---|---|---|
| **案 1** | 公開 API のみで依存深度まで tail する | 1 ステップ 334ms → 約 270ms（**目標 5ms に届かない**） |
| **案 2（推奨）** | `__all__` へ 2 関数を追記して公開し、末尾 1 点だけ計算する | 1 ステップ < 5ms |

案 2 は `__all__` への**追記のみ**で、計算式・分岐・境界は 1 文字も変えない。既存の呼び出し側への影響はゼロ（公開名が増えるだけ）。案 1 では本 Issue は解決しない。

`moving_averages` / `ma_marod` は `*_on_buffer` が既に公開済み（`__all__` に在席）のため、追加公開は不要。

### 5.4 配置（既存の層規約に一致）

| 層 | ファイル | 責務 |
|---|---|---|
| adapter | `adapter/compute/latest_meta.py` | archetype に `incremental` を追加。`LatestMeta` に `incremental` フィールド（状態器の記述子）を追加 |
| adapter | `adapter/compute/incremental_state.py`（**新規**） | 状態キャッシュ（LRU）と `step` / `advance` の呼び出し規約。指標の中身は知らない |
| adapter | `adapter/compute/incremental/`（**新規**） | 指標ごとの増分器。`src` の公開関数のみを呼ぶ（`*_on_buffer` / `_window_end_scalar` / `realized_coverage_latest` 等） |
| adapter | `adapter/compute/call_binding.py` | 各指標の `latest_meta` 宣言に増分器を追加（1 指標 1 行） |
| adapter | `adapter/compute/latest_dispatch.py` | `archetype == "incremental"` の分岐を 1 つ追加。既存 2 分岐は不変 |

**`indigators/*/src` は一切変更しない。** 増分器は `src` の公開関数を呼ぶだけであり、計算式を書き写さない（写した時点で参照実装との二重定義になり、本 Issue の再発源になる）。

---

## 6. 検証（各段階の通過条件）

ISSUE-158 で確立した方式（現行実装を参照実装として固定）をそのまま用いる。

### 6.1 一致検証（必須・全段階）

- **判定：`full` との全系列 `max_dev = 0`**（浮動小数の完全一致）。
- 対象：実データ `jp225_tick`、全時間足 9 種、窓長 25 サンプル、対象指標の全系列（`evq` 4 系列・`q5`/`q95`・`hlines` を含む）。
- 足内更新の検証を追加する：**同一の確定状態から形成中バーを変えて `step` を 10 回呼び、毎回 `mode='latest'`（現行実装）と一致すること**（§5.3.2 の非破壊性を固定する）。
- 一致しない段階はリリースしない。`sma`/`lwma` の浮動小数ドリフト（~1e-15）を許容する設計は採らない（§5.2）。

### 6.2 性能検証（必須・全段階）

- **判定：1 ステップ所要 < 5ms**（実 HTTP 経路・窓 1386 本・現行実測 159〜334ms から 2 桁改善）。
- 実 UI 実測：`simulator/replay_ui/tools/replay_diag.js` で更新粒度を数える。**判定：実測構成（7 指標）で 1 足あたりの指標更新回数がローソク更新回数と一致する**（1h・1分OHLC で 201 回）。

### 6.3 回帰

- 既存テスト全通過（現行：replay_ui JS 286 / indicator_ui JS 957 / Python 192）。
- 未宣言指標の応答が 1 ビットも変わらないこと（`incremental` 宣言が無い経路は不変）。

---

## 7. 本設計が採らないもの（応急処置の明示的排除）

| 案 | 却下理由 |
|---|---|
| ステップ数の予算制（実装後に撤回済み） | 原因を除去せず、指標を足すほど粒度が黙って落ちる。劣化を自動化・不可視化する |
| `MAX_FORMING_STEPS` の調整 | 同上。粒度の上限を人手で決めるだけ |
| `min_window` を経験的に短く切る | 依存深度より短くすると値が変わる。§4.3 の深度を下回る短縮は禁止 |
| 浮動小数の許容誤差を設けて tail を短縮 | 「full と完全一致」の保証を失う。`sma`/`lwma` は §5.2 のとおり増分器で解く |
| バックエンドのマルチスレッド化・プロセス分離 | 計算量を減らさない。必要並列数は 17〜70（実測）で 10 コアでは不足。rpy2 のスレッド親和もある |

---

## 8. 承認事項

| ID | 事項 | 状態 |
|---|---|---|
| B-1 | 抜本的解決＝`latest` の真の増分化で進める | **承認済み**（2026-08-01） |
| B-2 | `btlm_trail/src/__init__.py` の `__all__` に `_window_end_scalar` / `_empirical_quantile_causal` を追加公開する（計算式は変更しない・追記のみ） | **要承認**（§5.5） |
| B-3 | 段階 S1〜S5 の順序と、S1 単独でのリリース可否 | 要承認 |
| B-4 | 状態キャッシュをサーバ側 compute worker 内に置く（LRU・上限 インスタンス数×2） | 要承認 |
| B-5 | 通過条件＝「`full` との全系列 `max_dev = 0`」かつ「1 ステップ < 5ms」 | 要承認 |
| B-6 | S5（イベント分位）は S1〜S4 後に再測定してから着手可否を判断する | 要承認 |
