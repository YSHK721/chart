# IS/OOS ウォークフォワード（Walk-Forward）詳細設計書

## 1. 文書情報

- 作成日：2026-06-20
- バージョン：v0.1.0
- 作成者：system-internal-design エージェント
- 上位設計（唯一の正）：`.doc/ISOOS_WALKFORWARD_BASIC_DESIGN.md` v0.2.0（全 Blocker/Critical/High/Medium 解消済）
- 参照（無改変・部品再利用元）：
  - SP1：`simulator/usecase/run_is_oos.py`／`simulator/tools/run_is_oos_cli.py`／`.doc/ISOOS_SIMPLE_SPLIT_*_DESIGN.md`
  - SP2：`simulator/usecase/optimize.py`／`simulator/usecase/optimize_ports.py`／`simulator/usecase/optimize_strategies.py`／`simulator/tools/optimize_cli.py`／`.doc/ISOOS_OPTIMIZATION_*_DESIGN.md`
  - committed：`simulator/usecase/models.py`（`BacktestStats`／`BacktestResult`）／`simulator/main/__init__.py`（`build_interactor`）
- 本書の位置付け：基本設計 v0.2.0 を「誰が実装しても同一結果になる決定論水準」へ落とし込む。クラス名・dataclass フィールド・メソッドシグネチャ・処理フロー・JSON/Markdown スキーマ・テスト設計を確定する。基本設計から逸脱しない。実コードで実証できる前提のみ採用する。
- 新規ファイル（2 本のみ・既存無改変）：
  - `simulator/usecase/walk_forward.py`（UC・domain のみ依存）
  - `simulator/tools/walk_forward_cli.py`（tools 入口・Composition Root）
- 変更履歴：
  - v0.1.0 (2026-06-20) 初版。基本設計 v0.2.0 の FR-W1..W9／NFR-WP1/WP2/WD1/WS1/WS2／B-1/B-2/C-1/C-2/H-1/M-1 を実装可能水準へ詳細化。

---

## 2. 基本設計から抽出した技術的課題と本書での解決方針

> 基本設計 §2.3「設計上の課題と技術的リスク」を実装観点で再掲し、本書での確定解を示す。すべて実コード実証済（§11 トレーサビリティ参照）。

| ID | 課題（基本設計） | 本書の確定解（実装水準） |
|---|---|---|
| 課題-W1 | SP2 公開 IF は `oos_stats`（窓集約済 `BacktestStats`）のみ返す。per-trade／equity_curve は返さない。 | `stitch_oos` は `BacktestStats` 集約に限定（§5.3）。連結不能指標（区分 C）は通期スカラとして**出力しない**。トレード列連結は TBD-W1 として本書スコープ外。 |
| 課題-W2（🟡-2） | random で `--seed`/`--n-samples` 未指定→`min(None,int)` TypeError＋`Random(None)` 非決定論。 | `walk_forward_cli.main()` 内で `_build_search_port` 呼出**前**に `parser.error(固定メッセージ)`（exit 2）で前置中断（§4.2・§6.4）。`optimize_cli`/`optimize_strategies` は無改変。 |
| 課題-W3 | 窓境界の時刻型整合（`datetime64`/`int` 混在）。 | tools 層で SP1 `normalize_time` を再利用し、UC へは正規化済時刻のみ渡す。窓算術は同型 timedelta／整数差分（§5.4・§6.2）。 |
| 課題-W4 | anchored の OOS 接続（step≠oos_span で隙間/重なり）。 | `step==oos_span` を既定。不一致は警告ログを出して許容（無音禁止・§6.5）。 |
| 課題-W5（B-2） | `search_space` 未知キーで `build_interactor(**{...,**params})` TypeError 必発。 | `walk_forward_cli.main()` 内で未知キーを列挙し `parser.error`（exit 2）で前置中断（§4.2・§6.4）。許容キー集合は §6.3 に確定列挙。 |

---

## 3. モジュール／クラス詳細設計（`walk_forward.py`）

> 基本設計 §6.1 の概念シグネチャを実装シグネチャへ確定する。UC は `from __future__ import annotations` 下で `dataclasses`／`typing`／既存 usecase（`optimize`／`run_is_oos`）／`statistics`（標準ライブラリ）のみ import する。**pandas／simulator.main／simulator.adapter／simulator.tools を import しない**（C-W4・§8 で ast 検証）。

### 3.1 import 規約（依存方向の確定）

```python
# simulator/usecase/walk_forward.py 冒頭
from __future__ import annotations

import statistics                          # 標準ライブラリ（中央値算出）
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from simulator.usecase.optimize import (   # SP2（同 usecase 層・内向き許容）
    OptimizeError,
    OptimizeRequest,
    OptimizeResult,
    optimize,
)
from simulator.usecase.optimize_ports import (
    ObjectivePort,
    ParameterSearchPort,
)
# 注: slice_is_bars 等の SP1 純関数は optimize 内部で使用済のため WF は直接 import 不要。
#     WF は schedule_windows で時刻境界を算出し optimize へ委譲する。
```

許可 import 集合（ホワイトリスト）：`statistics`／`dataclasses`／`typing`／`simulator.usecase.optimize`／`simulator.usecase.optimize_ports`。これ以外の `simulator.*` import および `pandas`／`numpy`（トップレベル）は **§8.1 の ast テストで禁止**する。窓算術は時刻オブジェクトの `+`／`<=` 演算子のみに依存し `numpy` を直接 import しない（時刻オブジェクト自身が `datetime64`/`int` であり演算子はそのオブジェクトに委譲される）。

### 3.2 例外クラス `WalkForwardError`

```python
class WalkForwardError(Exception):
    """WF が結果を出せない明示中断（無音禁止・基本設計 §4.5・FR-W7）。

    送出条件:
      (a) 窓 0 件（schedule_windows が i=0 で終了条件不成立）
      (b) 総 run 見積り > max_total_runs（NFR-WP2）
      (c) ある窓で OptimizeError（既定＝厳格中断・窓 ID 付与で昇格）
    context（dict）に中断理由の内訳を載せる（global 境界・span・step・窓別 theoretical_count 等）。
    """

    def __init__(self, message: str, *, context: "dict | None" = None) -> None:
        super().__init__(message)
        self.context = context or {}
```

設計根拠：SP2 `OptimizeError`（`optimize.py:30-43`）と同型の「message＋context dict」契約を踏襲（学習コスト・整合コスト最小）。代替案＝標準 `ValueError` 流用は context 内訳を構造的に運べず無音禁止規約（NFR-WD1 監査ログ）に不適合のため棄却。

### 3.3 dataclass 定義

#### 3.3.1 `WindowSpec`（中間・1 窓の区間定義）

```python
@dataclass(frozen=True)
class WindowSpec:
    """1 窓の半開区間定義（基本設計 §5.2 WindowSpec・H-2/H-3）。

    IS_i = [is_start, split)、OOS_i = [split, oos_end)。
    split は IS 終端＝OOS 始端。frozen で決定論的かつ不変。
    """
    index: int           # 窓番号 i = 0, 1, 2, ...
    is_start: Any        # IS 始端（時刻型 numpy.datetime64 | int）
    split: Any           # IS 終端 = OOS 始端（slice_is_bars の split に一致）
    oos_end: Any         # OOS 終端（半開区間の右端・exclusive）
```

`frozen=True` 根拠：窓スケジュールは決定論的算術の結果であり、生成後の変更を型レベルで禁止する（NFR-WD1）。SP2 `OptimizeRequest`（`optimize.py:46` `@dataclass(frozen=True)`）に整合。

#### 3.3.2 `WalkForwardRequest`（入力一式）

```python
@dataclass(frozen=True)
class WalkForwardRequest:
    """WF の入力一式（基本設計 §5.2 WalkForwardRequest）。

    時刻・span は tools 層で normalize 済（UC は正規化済値のみ受領・課題-W3）。
    search_space のキーは build_interactor 受理キーワードの部分集合（B-2・tools で前置検証済）。
    """
    mode: str                          # "anchored" | "rolling"
    global_start: Any                  # 全期間始端（時刻型）
    global_end: Any                    # 全期間終端（時刻型・半開区間右端）
    is_span: Any                       # IS 幅（時刻 span・既定型 L-1）
    oos_span: Any                      # OOS 幅（時刻 span）
    step: Any                          # 前進量（時刻 span）
    search_space: "Mapping[str, list]"  # SP2 OptimizeRequest へ素通し
    max_total_runs: int                # 総 run 上限（必須・NFR-WP2）
    metric_names: "tuple[str, ...]" = (
        "profit", "profit_factor", "recovery_factor",
        "expected_payoff", "sharpe_ratio", "trades",
    )                                  # SP2 OptimizeRequest 既定に一致（optimize.py:53-60）
    efficiency_metric: str = "profit"  # WF 効率対象指標（C-1・profit 固定）
```

設計根拠：`metric_names` 既定は SP2 `OptimizeRequest.metric_names`（`optimize.py:53-60`）と**バイト一致**で複製（窓別 `OptimizeRequest` へそのまま渡すため整合必須）。`efficiency_metric` は C-1 で `profit` 固定だが、将来の TBD-W2（単一スカラ定義）拡張を阻害しないようフィールド化し既定値を `profit` に固定する（基本設計 §4.2 WF-F5 の「profit 一意固定」を既定値で表現）。

#### 3.3.3 `WindowResult`（窓別結果）

```python
@dataclass
class WindowResult:
    """窓 i の結果（WindowSpec × SP2 OptimizeResult のペア・基本設計 §5.2）。"""
    window: WindowSpec
    best_params: "Mapping[str, Any]"   # = OptimizeResult.best_params
    is_stats: Any                       # = OptimizeResult.best_is_stats（BacktestStats）
    oos_stats: Any                      # = OptimizeResult.oos_stats（BacktestStats）
    degradation: Any                    # = OptimizeResult.degradation（DegradationReport）
    optimize_result: OptimizeResult     # 完全な SP2 結果（trials/excluded_count 等を保持）
```

設計根拠：基本設計 §5.2 `WindowResult{window, best_params, is_stats, oos_stats, degradation}` の 5 属性を明示フィールド化しつつ、`optimize_result` を保持して SP2 の `excluded_count`／`total_candidates`／`finite_candidates`（監査ログ・§7.4）を欠落させない。`best_params`/`is_stats`/`oos_stats`/`degradation` は `optimize_result` からの射影（冗長だが基本設計の属性契約を直接表現）。代替案＝`optimize_result` のみ保持し都度射影は、基本設計の `WindowResult` 属性契約を型で表現できず実装者が迷うため棄却。

#### 3.3.4 `StitchedOosSummary`（通期 OOS 連結集約・M-1 3 分類）

```python
@dataclass
class StitchedOosSummary:
    """通期 OOS 連結集約（基本設計 §4.2 WF-F3・M-1 3 分類）。

    (A) 加法総和可：窓別値を単純総和。
    (B) 母数再計算可：A の総和母数から再計算（窓別比率の平均ではない）。
    (C) 連結不能：通期スカラを持たず窓別系列のみ（BacktestStats だけでは通期再計算不能）。
    """
    # (A) 加法総和（Σ）— 通期スカラ
    additive: "dict[str, float]"        # キー = 区分 A フィールド名、値 = Σ_i 窓別値
    # (B) 母数再計算 — 通期スカラ（None あり）
    recomputed: "dict[str, float | None]"  # profit_factor/expected_payoff/average_*_trade
    # (C) 連結不能 — 窓別系列のみ（通期スカラ非出力）
    per_window: "dict[str, list]"       # キー = 区分 C フィールド名、値 = 各窓スカラの列
    window_count: int                   # 連結に用いた窓数（= len(oos_stats_list)）
```

#### 3.3.5 `WfEfficiency`（WF 効率集約・C-1）

```python
@dataclass
class WfEfficiency:
    """WF 効率（基本設計 §4.2 WF-F5・C-1 profit 固定・None 除外）。"""
    metric: str                         # 対象指標名（= "profit"）
    per_window_ratio: "list[float | None]"  # 窓別 degradation.by_name("profit").ratio（None 窓含む）
    finite_ratios: "list[float]"        # None を除いた有限 ratio のみ
    excluded_none_count: int            # ratio is None の窓数（C-1 件数ログ）
    median: "float | None"              # finite_ratios の中央値（空なら None）
    minimum: "float | None"             # finite_ratios の最小値（空なら None）
```

#### 3.3.6 `WalkForwardResult`（WF 全結果・出力）

```python
@dataclass
class WalkForwardResult:
    """WF 全結果（基本設計 §5.2 WalkForwardResult）。"""
    windows: "list[WindowSpec]"             # schedule_windows の出力（窓列）
    window_results: "list[WindowResult]"    # 窓別 best/IS/OOS/劣化
    stitched_oos: StitchedOosSummary        # 通期 OOS 連結集約
    wf_efficiency: WfEfficiency             # WF 効率（profit ratio 集約）
    excluded: "dict[str, Any]"              # 除外/見積りメタ（総 run 見積り・窓数・除外件数）
```

設計根拠：基本設計 §5.2 `WalkForwardResult{windows, stitched_oos, wf_efficiency, excluded}` の 4 属性に `window_results`（窓別レポート FR-W5 の素材）を追加。`excluded` には `{"total_run_estimate", "window_count", "per_window_theoretical_count", "efficiency_excluded_none"}` を格納し、監査ログ（§7.4）と JSON 出力（§5.5）の両方の素材とする。

### 3.4 公開関数シグネチャ

#### 3.4.1 `schedule_windows`（純関数・窓スケジューラ）

```python
def schedule_windows(
    *,
    mode: str,
    global_start: Any,
    global_end: Any,
    is_span: Any,
    oos_span: Any,
    step: Any,
) -> "list[WindowSpec]":
    """窓列を決定論生成する純関数（FR-W1/W2・H-2/H-3）。

    副作用なし・入力不変。窓 i = 0,1,2,... を終了条件成立の限り採番し、
    最初に不成立となった i で打ち切る（単調性）。窓 0 件は WalkForwardError。
    """
```

**窓境界の式（基本設計 §4.2 WF-F1・§5.2 と完全一致）**：
- rolling：`is_start_i = global_start + i*step`、`split_i = is_start_i + is_span`、`oos_end_i = split_i + oos_span`
- anchored：`is_start_i = global_start`、`split_i = global_start + is_span + i*step`、`oos_end_i = split_i + oos_span`

**終了条件（`<=`・H-3）**：窓 i は次を満たす場合**のみ**採用し、満たさなくなった最初の i で打ち切る。
- rolling：`global_start + i*step + is_span + oos_span <= global_end`
- anchored：`global_start + is_span + i*step + oos_span <= global_end`
- 両式とも `oos_end_i <= global_end` と等価（H-2 端数切り捨て：`oos_end_i <= global_end` を満たさない部分 OOS 窓は不採用）。

**境界判定の決定論アルゴリズム**：

```
def schedule_windows(...):
    windows = []
    i = 0
    while True:
        if mode == "rolling":
            is_start = global_start + i * step
            split    = is_start + is_span
            oos_end  = split + oos_span
        elif mode == "anchored":
            is_start = global_start
            split    = global_start + is_span + i * step
            oos_end  = split + oos_span
        else:
            raise WalkForwardError("mode must be 'anchored' or 'rolling'",
                                   context={"mode": mode})
        if not (oos_end <= global_end):     # H-3 終了条件（<=）
            break                            # 単調性：以降の i も不成立
        windows.append(WindowSpec(index=i, is_start=is_start, split=split, oos_end=oos_end))
        i += 1
    if not windows:                          # 窓 0 件 → 明示中断（FR-W7）
        raise WalkForwardError(
            "no window satisfies the schedule (global span < is_span + oos_span)",
            context={"mode": mode, "global_start": global_start, "global_end": global_end,
                     "is_span": is_span, "oos_span": oos_span, "step": step},
        )
    return windows
```

**境界規約の確定（実装者が迷わないための一意化）**：
- `i * step` の評価：`step` が `numpy.timedelta64` の場合 `i * step` は Python `int * timedelta64` で決定論。`step` が `int`（epoch 秒差）の場合 `i * step` は整数乗算で決定論。いずれも浮動小数等価比較を経由しない（NFR-WD1・SP2 High-3 継承）。
- `oos_end == global_end` ちょうど：`<=` のため**採用**（半開区間 `[is_start, oos_end)` は `global_end` を含まないが、右端が `global_end` に一致する窓は OOS 幅が `oos_span` に等しく端数でないため採用）。この境界は §8.1 単体テスト `test_schedule_boundary_inclusive_oos_end_equals_global_end` で固定する。
- 前提違反（`global_start > global_end`・`is_span <= 0` 等）：本関数は終了条件不成立→窓 0 件→`WalkForwardError` で吸収する（i=0 で `oos_end > global_end` または比較不能になる）。型不正（mode 不一致）は即時 `WalkForwardError`。

設計根拠：`while True` ＋ break は「終了条件不成立の最初の i で単調に打ち切る」H-3 の単調性を最も素直に表現する。代替案＝事前に窓数 W を閉形式（`(global_end - global_start - is_span - oos_span) // step + 1`）で算出する方式は、時刻型が `datetime64`/`int` 混在で `//`（floor 除算）の挙動が型依存になり決定論の実証コストが上がるため棄却。逐次判定は時刻型の `+`/`<=` 演算子のみに依存し、両型で同一ロジックが成立する（型分岐不要）。

#### 3.4.2 `stitch_oos`（純関数・OOS 連結・M-1 3 分類）

```python
def stitch_oos(oos_stats_list: "list[Any]") -> StitchedOosSummary:
    """窓別 OOS BacktestStats を M-1 3 分類で連結集約する純関数（FR-W4・WF-F3）。

    副作用なし。入力＝窓順の oos_stats（BacktestStats）列。
    空列は WalkForwardError（連結対象 0 件＝窓 0 件と整合・無音禁止）。
    """
```

**M-1 3 分類フィールド表（`models.py:91-143` 実フィールドと 1:1・基本設計 §4.2 WF-F3 と一致）**：

区分 A（加法総和可・Σ で通期値）：
```
_ADDITIVE_FIELDS = (
    "profit", "gross_profit", "gross_loss",
    "trades", "profit_trades", "loss_trades",
    "long_trades", "short_trades",
    "profit_long_trades", "profit_short_trades",
)
```

区分 B（母数再計算可・A の総和から再計算）：
```
recomputed["profit_factor"]        = (Σgross_profit / Σgross_loss) if Σgross_loss != 0 else None
recomputed["expected_payoff"]      = (Σprofit / Σtrades)           if Σtrades != 0     else None
recomputed["average_profit_trade"] = (Σgross_profit / Σprofit_trades) if Σprofit_trades != 0 else None
recomputed["average_loss_trade"]   = (Σgross_loss / Σloss_trades)     if Σloss_trades != 0   else None
```

区分 C（連結不能・窓別系列のみ・通期スカラ非出力）：
```
_NON_STITCHABLE_FIELDS = (
    "initial_deposit", "recovery_factor", "sharpe_ratio", "z_score", "ahpr",
    "balance_min", "balance_dd", "balance_dd_percent",
    "balance_dd_relative", "balance_ddrel_percent", "balance_dd_abs",
    "max_profit_trade", "max_loss_trade",
    "max_con_wins", "max_con_profit_trades", "max_con_losses", "max_con_loss_trades",
    "con_profit_max", "con_profit_max_trades", "con_loss_max", "con_loss_max_trades",
    "profit_trades_avg_con", "loss_trades_avg_con",
    "equity_dd_abs", "equity_dd_max", "equity_dd_max_percent",
)
```

**連結アルゴリズム**：

```
def stitch_oos(oos_stats_list):
    if not oos_stats_list:
        raise WalkForwardError("stitch_oos received empty oos_stats list",
                               context={"window_count": 0})
    # (A) 加法総和
    additive = {f: 0.0 for f in _ADDITIVE_FIELDS}
    for s in oos_stats_list:
        for f in _ADDITIVE_FIELDS:
            additive[f] += float(getattr(s, f))
    # (B) 母数再計算（A の総和から・窓別比率の平均ではない）
    sgp, sgl = additive["gross_profit"], additive["gross_loss"]
    st, spt, slt = additive["trades"], additive["profit_trades"], additive["loss_trades"]
    recomputed = {
        "profit_factor":        (sgp / sgl) if sgl != 0.0 else None,
        "expected_payoff":      (additive["profit"] / st) if st != 0.0 else None,
        "average_profit_trade": (sgp / spt) if spt != 0.0 else None,
        "average_loss_trade":   (sgl / slt) if slt != 0.0 else None,
    }
    # (C) 連結不能 → 窓別系列のみ（通期スカラ非出力）
    per_window = {f: [float(getattr(s, f)) for s in oos_stats_list]
                  for f in _NON_STITCHABLE_FIELDS}
    return StitchedOosSummary(additive=additive, recomputed=recomputed,
                              per_window=per_window, window_count=len(oos_stats_list))
```

**3 分類の不変条件（§8.1 で固定）**：`set(_ADDITIVE_FIELDS) | set(recomputed.keys()) | set(_NON_STITCHABLE_FIELDS)` は `BacktestStats` の全フィールド集合と一致し、3 集合は互いに素である（フィールド漏れ・重複分類を ast/`dataclasses.fields` で検証＝`test_stitch_classification_covers_all_fields`）。これにより「加法不能を加法に入れる」等の分類誤りを実装時に機械検出する。

設計根拠（B の「母数再計算」が窓別平均でない理由）：`profit_factor` は比率指標であり窓別 `profit_factor_i` の単純平均は通期 `profit_factor` と一致しない（`Σgross_profit / Σgross_loss ≠ mean(gross_profit_i / gross_loss_i)`）。SP1 `build_degradation_report`（`run_is_oos.py:104-106`）の比率算出と整合させ、通期母数（A の総和）からの再計算とする。`Σgross_loss == 0` で None（ゼロ除算回避）は SP1 の `iv != 0.0 else None`（`run_is_oos.py:106`）規約に整合。

#### 3.4.3 `aggregate_efficiency`（純関数・WF 効率・C-1）

```python
def aggregate_efficiency(
    window_results: "list[WindowResult]",
    *,
    metric: str = "profit",
) -> WfEfficiency:
    """窓別 degradation の profit ratio を C-1 規約で集約する純関数（WF-F5・FR-W6）。

    ratio is None（IS profit=0）の窓は集約から除外し件数を記録する。
    中央値・最小値は有限 ratio のみから算出（None を 0/欠損として混入させない）。
    """
```

**集約アルゴリズム**：

```
def aggregate_efficiency(window_results, *, metric="profit"):
    per_window_ratio = []
    for wr in window_results:
        md = wr.degradation.by_name(metric)        # DegradationReport.by_name（run_is_oos.py:75-79）
        per_window_ratio.append(md.ratio if md is not None else None)
    finite = [r for r in per_window_ratio if r is not None]   # C-1: None 除外
    excluded = len(per_window_ratio) - len(finite)
    median  = statistics.median(finite) if finite else None
    minimum = min(finite) if finite else None
    return WfEfficiency(metric=metric, per_window_ratio=per_window_ratio,
                        finite_ratios=finite, excluded_none_count=excluded,
                        median=median, minimum=minimum)
```

設計根拠：`md.ratio` の None 条件は SP1 `ratio = (ov / iv) if iv != 0.0 else None`（`run_is_oos.py:106`）で IS profit==0 のとき None。C-1 規約「None 窓除外＋件数ログ・有限 ratio のみ集約」を `finite` フィルタと `excluded_none_count` で表現。全窓 None（`finite==[]`）で median/minimum は None（基本設計 §4.2 WF-F5「全窓 None→集約値 None＋件数明示」に一致）。`statistics.median` は標準ライブラリ（C-W3 技術スタック追加なし）。

#### 3.4.4 `walk_forward`（オーケストレーション本体）

```python
def walk_forward(
    *,
    request: WalkForwardRequest,
    window_bars_provider: "Callable[[Any, Any], Any]",
    make_run_segment: "Callable[[Mapping[str, Any]], Callable[[Any, Any], Any]]",
    search_port: ParameterSearchPort,
    objective_port: ObjectivePort,
) -> WalkForwardResult:
    """WF オーケストレーション本体（基本設計 §4.3 処理フロー・§6.1）。

    window_bars_provider(is_start, oos_end) -> 当窓 full バー列（B-1：UC が optimize へ
        渡す full_bars は本コールバックの戻り値のみ。tools の全期間 full_bars は渡さない）。
    make_run_segment/search_port/objective_port は SP2 optimize へ素通し転送。
    """
```

**処理フロー（基本設計 §4.3 と完全一致・実装擬似コード）**：

```
def walk_forward(*, request, window_bars_provider, make_run_segment, search_port, objective_port):
    # 1. 窓スケジュール生成（窓 0 件 → WalkForwardError・FR-W7）
    windows = schedule_windows(
        mode=request.mode, global_start=request.global_start, global_end=request.global_end,
        is_span=request.is_span, oos_span=request.oos_span, step=request.step,
    )
    # 2. 総 run 見積り（H-1 Port 契約：theoretical_count == 実 IS run 数）
    per_window_tc = [search_port.theoretical_count(request.search_space) for _ in windows]
    total_run_estimate = sum(per_window_tc) + len(windows)   # Σ_i tc_i + W（各窓 OOS 1 回）
    if total_run_estimate > request.max_total_runs:          # NFR-WP2 拒否
        raise WalkForwardError(
            "total run estimate exceeds max_total_runs",
            context={"total_run_estimate": total_run_estimate,
                     "max_total_runs": request.max_total_runs,
                     "window_count": len(windows),
                     "per_window_theoretical_count": per_window_tc},
        )
    # 3. 各窓で optimize（IS 探索 → best → OOS）
    window_results: list[WindowResult] = []
    for w in windows:
        bars_i = window_bars_provider(w.is_start, w.oos_end)   # B-1: 当窓 full のみ
        try:
            res_i = optimize(
                request=OptimizeRequest(
                    search_space=request.search_space, split=w.split,
                    is_trading_start=w.is_start, metric_names=request.metric_names,
                ),
                full_bars=bars_i, make_run_segment=make_run_segment,
                search_port=search_port, objective_port=objective_port,
            )
        except OptimizeError as exc:                            # 窓 ID 付与で昇格（既定厳格中断）
            raise WalkForwardError(
                f"optimize failed at window index={w.index}",
                context={"window_index": w.index, "optimize_context": exc.context,
                         "optimize_message": str(exc)},
            ) from exc
        window_results.append(WindowResult(
            window=w, best_params=res_i.best_params, is_stats=res_i.best_is_stats,
            oos_stats=res_i.oos_stats, degradation=res_i.degradation, optimize_result=res_i,
        ))
    # 4. OOS 連結
    stitched = stitch_oos([wr.oos_stats for wr in window_results])
    # 5. WF 効率
    eff = aggregate_efficiency(window_results, metric=request.efficiency_metric)
    # 6. 結果構築
    return WalkForwardResult(
        windows=windows, window_results=window_results, stitched_oos=stitched,
        wf_efficiency=eff,
        excluded={"total_run_estimate": total_run_estimate, "window_count": len(windows),
                  "per_window_theoretical_count": per_window_tc,
                  "efficiency_excluded_none": eff.excluded_none_count},
    )
```

**B-1 の確定（二重 full_bars 調停・実装水準）**：`walk_forward` は `full_bars` 引数を**持たない**。`optimize(full_bars=...)` に渡すのは `window_bars_provider(w.is_start, w.oos_end)` の戻り値（当窓 full・`[is_start, oos_end)`）のみ。tools 層（§4）が `make_run_segment_factory` の戻り値 `full_bars`（全期間）を `window_bars_provider` のスライス元として保持するが、UC へは渡さない。これにより `optimize` 内 `slice_is_bars(full_list, split)`（`optimize.py:111`）と `rs_best(full_list, split)`（`optimize.py:188`）が当窓 full を母集合として窓内 IS/OOS を正しく分割する（§11 PB1-2 実証）。

**`is_trading_start=w.is_start` の妥当性**：`optimize` 事前検証 `is_trading_start <= split`（`optimize.py:125`）に対し、`w.is_start <= w.split`（`split = is_start + is_span`、`is_span >= 1`）が常に成立するため検証を通過する。

---

## 4. tools 入口詳細設計（`walk_forward_cli.py`）

> Composition Root。pandas／simulator.main の import を許容（SP1/SP2 tools と同層）。SP1 `normalize_time`／`assert_safe_output_dir`／`make_run_segment`、SP2 `make_run_segment_factory`／`_build_objective_port` を**再利用（無改変呼出）**する。

### 4.1 再利用部品（無改変）

| 部品 | 出所 | 用途 |
|---|---|---|
| `assert_safe_output_dir(out_dir, repo_root)` | `run_is_oos_cli.py:67` | 出力先ガード（C-W1・NFR-WS1） |
| `normalize_time(value, sample_bar_time)` | `run_is_oos_cli.py:54` | CLI 文字列→時刻型正規化（課題-W3） |
| `make_run_segment_factory(base_kwargs, *, split_str, is_trading_start_str)` | `optimize_cli.py:26` | params→run_segment ファクトリ＋全期間 full_bars 取得 |
| `_build_objective_port(args)` | `optimize_cli.py:69` | 目的 Port 選択 |
| `GridSearch`／`RandomSearch` | `optimize_strategies.py:63,81` | 探索 Port |

注：SP2 `make_run_segment_factory` は `split_str`/`is_trading_start_str` を必須引数に取るが、WF では窓ごとに split/is_trading_start が変わるため、これらは「factory 構築の時刻正規化サンプル取得」目的でのみダミー値（例：`global_start_str`）を渡す。**factory が返す `make_run_segment` は split/is_trading_start を閉包せず**、`make_run_segment` が返す `run_segment(bars, trading_start)` が run 時に `request.bars`/`request.trading_start` を引数で上書きする（`run_is_oos_cli.py:45-47`）ため、factory 構築時の split/is_trading_start は run に波及しない（§11 PB1-3 実証）。WF は factory 戻り値のうち `factory`（=make_run_segment）と `full_bars` のみ使用し、`split`/`is_trading_start`（全期間用ダミー）は破棄する。

### 4.2 `main()` 処理フロー（入口検証を含む）

```
def main(argv=None, *, repo_root=None) -> int:
    parser = _build_arg_parser()                      # ArgumentParser をローカル保持（parser.error 用）
    args = parser.parse_args(argv)

    # --- 入口検証 1：🟡-2（C-2・FR-W8）— _build_search_port 呼出前 ---
    if args.search_algo == "random" and (args.seed is None or args.n_samples is None):
        parser.error(
            "--search-algo random requires both --seed and --n-samples "
            "(omitting either is non-deterministic)")              # exit code 2

    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]
    out_dir = assert_safe_output_dir(args.out_dir, repo_root)      # 出力先ガード（先頭付近）

    base_kwargs = _build_base_kwargs(args)                         # SP2 と同形
    search_space = _parse_search_space(args.search_param)

    # --- 入口検証 2：B-2 未知探索キー（FR-W9）— factory/optimize 呼出前 ---
    unknown = set(search_space.keys()) - _BUILD_INTERACTOR_KEYWORDS
    if unknown:
        parser.error(
            f"unknown search-param key(s): {sorted(unknown)} "
            "(must be a subset of build_interactor keywords)")     # exit code 2

    # --- factory 構築（SP2 再利用・全期間 full_bars 取得）---
    factory, full_bars, _split_dummy, _is_start_dummy = make_run_segment_factory(
        base_kwargs, split_str=args.global_start, is_trading_start_str=args.global_start)

    sample_time = full_bars[0].time
    global_start = normalize_time(args.global_start, sample_time)
    global_end   = normalize_time(args.global_end, sample_time)
    is_span  = _normalize_span(args.is_span, sample_time)          # §4.3
    oos_span = _normalize_span(args.oos_span, sample_time)
    step     = _normalize_span(args.step, sample_time)

    # --- anchored OOS 接続チェック（課題-W4・無音禁止）---
    if step != oos_span:
        sys.stderr.write(
            f"[warn] step ({args.step}) != oos_span ({args.oos_span}): "
            "OOS windows overlap or gap.\n")

    # --- window_bars_provider（B-1：当窓 full スライス・pandas を UC に持ち込まない DI）---
    def window_bars_provider(is_start, oos_end):
        return [b for b in full_bars if is_start <= b.time < oos_end]

    request = WalkForwardRequest(
        mode=args.mode, global_start=global_start, global_end=global_end,
        is_span=is_span, oos_span=oos_span, step=step,
        search_space=search_space, max_total_runs=args.max_total_runs)

    result = walk_forward(
        request=request, window_bars_provider=window_bars_provider,
        make_run_segment=factory, search_port=_build_search_port(args),
        objective_port=_build_objective_port(args))

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "walk_forward.json").write_text(
        json.dumps(to_json_dict(result, request), indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(to_markdown(result, request), encoding="utf-8")
    return 0
```

**`window_bars_provider` の正当性**：`[b for b in full_bars if is_start <= b.time < oos_end]` は当窓 full（半開区間 `[is_start, oos_end)`）を返す。`full_bars` は時刻昇順（committed 規約）であり、結果も昇順を保つため `optimize` 内 `slice_is_bars`（`run_is_oos.py:34-38` の昇順前提 head-prefix）と整合する。pandas を使わず list 内包で実装し UC へは正規化済時刻のコールバックのみ渡す（C-W4・SP2 `make_run_segment` と同じ DI 思想）。

### 4.3 `_normalize_span` と `_BUILD_INTERACTOR_KEYWORDS`

```python
def _normalize_span(value: str, sample_bar_time: Any) -> Any:
    """span 文字列を時刻型と整合する差分へ正規化（L-1・課題-W3）。

    sample_bar_time が int（epoch 秒）なら int(秒数)、
    numpy.datetime64 系なら pandas.Timedelta(value).to_timedelta64() を返す。
    """
    import pandas as pd
    if isinstance(sample_bar_time, int) and not isinstance(sample_bar_time, bool):
        return int(pd.Timedelta(value).total_seconds())
    return pd.Timedelta(value).to_timedelta64()
```

設計根拠：`normalize_time`（`run_is_oos_cli.py:54-64`）が時刻点を正規化するのに対し、span は**期間**であり `pd.Timedelta` で正規化する。`int` 時刻型なら秒数 int（`global_start + i*step` が整数加算）、`datetime64` なら `timedelta64`（`global_start + i*step` が `datetime64 + timedelta64`）。両型とも `schedule_windows` の `+`/`<=` が決定論的に成立する（§3.4.1）。pandas は tools 層に閉じる（C-W4）。

```python
# build_interactor（main/__init__.py:256-285）の受理キーワード集合（B-2 許容キー）。
# data_path/symbol/period/ea_name 等の base 系も含む全 keyword 集合の写し。
# search_space は探索対象部分集合のみ与える運用だが、検証は build_interactor 受理キー全体で行う。
_BUILD_INTERACTOR_KEYWORDS = frozenset({
    "data_path", "symbol", "period", "ea_name", "initial_deposit", "contract_size",
    "volume_min", "volume_max", "volume_step", "stops_level", "digits", "point_size",
    "leverage", "ma_period", "ma_method", "lot_size", "stop_loss_points",
    "take_profit_points", "config_overrides", "stop_out_level", "slope_shift",
    "slope_min_points", "entry_offset_points", "entry_type", "trading_start",
    "tick_store_root", "tick_start", "tick_end",
})
```

設計根拠：`build_interactor`（`main/__init__.py:256-285`）の全 keyword（`*` 後・catch-all なし）を写経。`search_space` の典型探索キー（`lot_size`／`stop_loss_points`／`take_profit_points`／`entry_offset_points`／`ma_period`／`ma_method`／`entry_type`／`stop_out_level`／`slope_shift`／`slope_min_points`）はすべてこの集合の部分集合（基本設計 §4.5 許容キー例と一致・§11 PB2-3 実証）。未知キーは `make_run_segment_factory` の `build_interactor(**{**base_kwargs, **params})`（`optimize_cli.py:51`）で TypeError 必発のため、その**前**に列挙中断する。`_BUILD_INTERACTOR_KEYWORDS` は実装時に `build_interactor` のシグネチャと突合する（§8.1 `test_keyword_whitelist_matches_build_interactor`）。

### 4.4 CLI 引数定義（`_build_arg_parser`）

SP2 `_build_arg_parser`（`optimize_cli.py:161-196`）の base 系引数（`--data-path`/`--ea-name`/`--symbol`/.../`--stop-out-level`/`--config-override`）と探索系（`--search-param`/`--search-algo`/`--seed`/`--n-samples`/`--max-candidates`/`--objective`）を踏襲し、WF 固有を追加：

| 引数 | 型 | 必須 | 用途 |
|---|---|---|---|
| `--mode` | choices=["anchored","rolling"] | 必須 | 窓方式 |
| `--global-start` | str | 必須 | 全期間始端（normalize_time で時刻型へ） |
| `--global-end` | str | 必須 | 全期間終端（半開区間右端） |
| `--is-span` | str | 必須 | IS 幅（pd.Timedelta 解釈・例 "30D"） |
| `--oos-span` | str | 必須 | OOS 幅 |
| `--step` | str | 必須 | 前進量（既定運用 step==oos_span） |
| `--max-total-runs` | int | 必須 | 総 run 上限（NFR-WP2） |

注：SP2 の `--split`/`--is-trading-start` は WF では窓が決めるため**廃止**（代わりに `--global-start`/`--global-end`/span 系）。base 系・探索系・`--out-dir`・`--objective`・`--max-candidates` は SP2 と同一意味で踏襲する。

---

## 5. 物理データモデル（出力 JSON/Markdown スキーマ）

> 基本設計 §6.4 の「JSON＋Markdown 二出力（SP1/SP2 `to_json_dict`/`to_markdown` 踏襲）」を確定スキーマ化する。出力先は `out_dir/walk_forward.json` と `out_dir/report.md`（SP2 の `optimize.json`/`report.md` 命名規則に整合）。

### 5.1 出力ファイル

| ファイル | 形式 | 内容 |
|---|---|---|
| `<out_dir>/walk_forward.json` | JSON（indent=2・SP2 踏襲） | 機械可読の全結果 |
| `<out_dir>/report.md` | Markdown | 人間可読の窓別表＋連結表＋効率表 |

### 5.2 `walk_forward.json` スキーマ（トップレベル）

```json
{
  "meta": {
    "mode": "rolling",
    "global_start": "<str>", "global_end": "<str>",
    "is_span": "<str>", "oos_span": "<str>", "step": "<str>",
    "objective": "net", "search_algo": "grid",
    "window_count": 12,
    "total_run_estimate": 156,
    "max_total_runs": 200,
    "efficiency_excluded_none": 1
  },
  "windows": [
    {
      "index": 0,
      "is_start": "<str(window.is_start)>",
      "split": "<str(window.split)>",
      "oos_end": "<str(window.oos_end)>",
      "best_params": { "lot_size": 0.2, "stop_loss_points": 100 },
      "is_stats": { "...BacktestStats asdict..." },
      "oos_stats": { "...BacktestStats asdict..." },
      "degradation": [
        {"name": "profit", "is_value": 1234.0, "oos_value": 567.0,
         "ratio": 0.459, "delta": -667.0}
      ],
      "excluded_count": {"nonfinite": 0, "failed": 0},
      "total_candidates": 12, "finite_candidates": 12
    }
  ],
  "stitched_oos": {
    "window_count": 12,
    "additive": {"profit": 6800.0, "gross_profit": 12000.0, "gross_loss": -5200.0,
                 "trades": 240, "profit_trades": 130, "loss_trades": 110,
                 "long_trades": 120, "short_trades": 120,
                 "profit_long_trades": 65, "profit_short_trades": 65},
    "recomputed": {"profit_factor": 2.307, "expected_payoff": 28.33,
                   "average_profit_trade": 92.3, "average_loss_trade": -47.27},
    "per_window": {"sharpe_ratio": [0.8, 1.1, ...], "equity_dd_max": [...], "...": []}
  },
  "wf_efficiency": {
    "metric": "profit",
    "per_window_ratio": [0.46, null, 0.71, "..."],
    "finite_ratios": [0.46, 0.71, "..."],
    "excluded_none_count": 1,
    "median": 0.58, "minimum": 0.31
  }
}
```

### 5.3 スキーマ確定規約

- `windows[].is_start`/`split`/`oos_end`：時刻型（`datetime64`/`int`）を JSON 化するため `str(...)` で文字列化（SP2 `to_markdown` が `f"- split: {split}"` で文字列化する方針に整合・決定論的文字列表現）。
- `windows[].is_stats`/`oos_stats`：`dataclasses.asdict(stats)`（SP1/SP2 `to_json_dict` の asdict パターン・`optimize_cli.py:91-93`）。`BacktestStats` 全フィールドを出力。
- `windows[].degradation`：`[asdict(m) for m in degradation.metrics]`（`optimize_cli.py:94`）。
- `stitched_oos.recomputed` の None：JSON では `null`（M-1 区分 B のゼロ除算 None）。
- `stitched_oos.per_window`（区分 C）：通期スカラを**持たず**窓別系列のみ（M-1 連結不能・虚偽連結禁止）。
- `wf_efficiency.per_window_ratio` の None：JSON `null`（C-1 None 窓を明示・件数は `excluded_none_count`）。
- **決定論**：JSON キー順は dict 挿入順（Python 3.7+ 保証）で固定。`json.dumps(..., indent=2)` は SP2 と同設定。同一入力で**バイト同一**（NFR-WD1・§8.3 回帰テスト）。

### 5.4 `report.md` スキーマ（Markdown・人間可読）

SP2 `to_markdown`（`optimize_cli.py:112-156`）の形式を踏襲し、WF 固有のセクションを構成：

```markdown
# IS/OOS Walk-Forward Report

- mode: rolling
- global: <global_start> .. <global_end>
- is_span / oos_span / step: 30D / 10D / 10D
- objective: net  search_algo: grid
- windows: 12  total_run_estimate: 156  max_total_runs: 200

## Per-Window Summary
| # | IS [start,split) | OOS [split,end) | best_params | IS profit | OOS profit | profit ratio |
|---|---|---|---|---|---|---|
| 0 | <is_start>..<split> | <split>..<oos_end> | {...} | 1234.0 | 567.0 | 0.459 |
...

## Stitched OOS (additive totals)
| metric | total |
|---|---|
| profit | 6800.0 |
...

## Stitched OOS (recomputed ratios)
| metric | value |
|---|---|
| profit_factor | 2.307 |
...

## WF Efficiency (profit ratio)
- excluded (IS profit=0): 1 window(s)
- median: 0.58   minimum: 0.31
| # | profit ratio |
|---|---|
| 0 | 0.459 |
| 1 | N/A |
...
```

- profit ratio 列の None：SP2 同様 `"N/A"`（`optimize_cli.py:141` の `"N/A" if m.ratio is None`）で表示。
- 区分 C（連結不能）は Markdown でも通期スカラ表に出さず、必要なら窓別系列を別表で（基本設計の「窓別系列のみ提示」に整合）。本書既定は連結不能指標を report.md の通期表に出さない。

### 5.5 `to_json_dict`／`to_markdown`（tools 出力整形関数）

```python
def to_json_dict(result: WalkForwardResult, request: WalkForwardRequest) -> dict: ...
def to_markdown(result: WalkForwardResult, request: WalkForwardRequest) -> str: ...
```

`asdict` 直適用ではなく明示構築（SP2 `to_json_dict`/`to_markdown` と同方針・`optimize_cli.py:87,112`）。理由：`WalkForwardResult` は `optimize_result`（trials 内 `is_stats` 等）を保持し `asdict` 全展開は冗長かつ JSON サイズ過大。SP2 同様、窓別は best/IS/OOS/degradation/excluded_count に射影して出力する。

---

## 6. クリーンアーキ準拠・依存方向の確定

### 6.1 レイヤ配置

| レイヤ | 新規ファイル | 依存先（許可） | 禁止 import |
|---|---|---|---|
| tools（Composition Root） | `walk_forward_cli.py` | `argparse`/`json`/`pathlib`/`sys`/`pandas`/`simulator.main`/SP1 tools/SP2 tools/`walk_forward`(UC) | （制約なし・入口層） |
| usecase（新 UC） | `walk_forward.py` | `statistics`/`dataclasses`/`typing`/`simulator.usecase.optimize`/`optimize_ports` | `pandas`/`numpy`(top)/`simulator.main`/`simulator.adapter`/`simulator.tools` |
| usecase（SP2・無改変） | （既存） | domain／SP1 純関数 | — |
| usecase（SP1・無改変） | （既存） | domain | — |
| domain（committed・無改変） | （既存） | なし | — |

### 6.2 依存方向（循環なし）

```
walk_forward_cli (tools) ──→ walk_forward (UC) ──→ optimize (SP2 UC) ──→ slice_is_bars 等 (SP1 UC) ──→ domain
        │                          │
        └─ pandas/main 許容        └─ domain のみ依存（pandas/main/adapter/tools 非 import）
```

- 逆向き依存なし（domain は usecase を知らない・SP2 は WF を知らない）。
- `walk_forward.py` の import は §3.1 ホワイトリストに限定し、§8.1 ast テストで `pandas`/`simulator.main`/`simulator.adapter`/`simulator.tools` の非 import を機械検証する。
- committed/SP1/SP2 の差分 **0 行**（NFR-WS2）。WF は新規 2 ファイルのみ（git diff で committed/SP1/SP2 ソース無変更を CI 確認）。

### 6.3 無改変の担保（C-W2）

| 既存ファイル | WF からの扱い |
|---|---|
| `optimize.py`/`optimize_ports.py`/`optimize_strategies.py` | import して呼ぶのみ（編集 0 行） |
| `optimize_cli.py` | `make_run_segment_factory`/`_build_objective_port` を import 再利用（編集 0 行） |
| `run_is_oos.py`/`run_is_oos_cli.py` | `normalize_time`/`assert_safe_output_dir`/`make_run_segment` を import 再利用（編集 0 行） |
| `models.py`/`main/__init__.py` | 参照のみ（編集 0 行） |

---

## 7. 非機能設計（実装水準）

### 7.1 性能（NFR-WP1/WP2）

- 総 engine run = `Σ_i theoretical_count_i + W`（H-1 Port 契約）。`walk_forward` step 2 で `per_window_tc = [search_port.theoretical_count(search_space) for _ in windows]` を算出し `sum(per_window_tc) + len(windows)` を `max_total_runs` と比較。超過で `WalkForwardError`（件数ログ・無音禁止）。
- 探索空間が窓間で不変（全窓同一 `search_space`）のため `theoretical_count` は全窓同値だが、Port 契約の一般性を保つため窓ごとに呼ぶ（将来の窓別 search_space 拡張に対し非破壊）。
- CSV 再ロード `W×(N_cand+1)` 回は SP2 `build_interactor` 機構由来（TBD-W4・本書スコープ外）。WF 層で虚偽の「1 回ロード」根拠は記載しない（SP2 High-1 教訓継承）。

### 7.2 決定論性（NFR-WD1）

- 窓境界：`schedule_windows` は時刻型の `+`/`<=` のみで決定論（浮動小数等価比較なし）。
- 窓内 optimize：SP2 決定論（辞書順序規約＋seed 固定 random・`optimize_strategies.py:18-37,105`）を継承。🟡-2 入口検証で seed 未指定（非決定論）を排除。
- 出力：`json.dumps(..., indent=2)` のキー順固定＋同一入力でバイト同一（§8.3 回帰テスト）。

### 7.3 明示中断（無音禁止）

| ケース | 中断手段 | 終了コード |
|---|---|---|
| 🟡-2（random seed/n_samples 未指定） | `parser.error`（tools） | 2 |
| B-2（未知 search_space キー） | `parser.error`（tools） | 2 |
| 窓 0 件 | `WalkForwardError`（UC） | 非ゼロ（未捕捉で 1） |
| 総 run 上限超過 | `WalkForwardError`（UC） | 非ゼロ |
| 窓内 OptimizeError | `WalkForwardError`（窓 ID 付与・UC） | 非ゼロ |

注：`parser.error` は exit 2（argparse 標準）。`WalkForwardError` を `main()` で捕捉して特定コードに変換するか未捕捉伝播させるかは実装方針だが、本書既定は**未捕捉伝播（SystemExit 既定 1 相当・トレースで context 露出）**とし、`parser.error` 経路（入口検証）のみ exit 2 を保証する（§8.1 でテスト）。

### 7.4 監査ログ・出力先ガード

- 実行前：窓数・総 run 見積り（`total_run_estimate`）を stderr/meta に出力。
- 各窓：`excluded_count`（SP2 `nonfinite`/`failed`）を窓別 JSON に記録。
- WF 効率：`excluded_none_count`（IS profit=0 窓数）を meta/efficiency に記録。
- 出力先：`assert_safe_output_dir`（`run_is_oos_cli.py:67`）で `marketdata/`/`fixtures/`/`confirmation/` 配下・repo_root 外を拒否（C-W1・NFR-WS1）。

---

## 8. テスト設計（単体／結合／回帰）

> カバレッジ目標：新規 2 ファイル（`walk_forward.py`／`walk_forward_cli.py`）の**行カバレッジ 95% 以上・分岐カバレッジ 90% 以上**。純関数（`schedule_windows`/`stitch_oos`/`aggregate_efficiency`）は **100%**（domain 依存のみ・engine 不要で全分岐到達可能）。自動化範囲：単体・回帰は pytest で完全自動化（engine fixture 不要）。結合は読み取り専用 confirmation fixture で自動化。

### 8.1 単体テスト（UC 純関数・engine 不要）

配置：`simulator/tests/unit/test_walk_forward.py`／`simulator/tests/unit/test_walk_forward_cli.py`。

| # | テスト名 | 検証内容 | 期待 |
|---|---|---|---|
| U-1 | `test_schedule_rolling_window_boundaries` | rolling 式 `is_start=gs+i*step` 等 | 窓列の各境界が式通り |
| U-2 | `test_schedule_anchored_window_boundaries` | anchored 式 `is_start=gs 固定`/`split=gs+is_span+i*step` | is_start 全窓不変・split 拡張 |
| U-3 | `test_schedule_boundary_inclusive_oos_end_equals_global_end` | `oos_end == global_end` ちょうど | **採用**（`<=`・H-3） |
| U-4 | `test_schedule_truncates_partial_oos` | 端数窓（`oos_end > global_end`） | 不採用（H-2 切り捨て） |
| U-5 | `test_schedule_monotone_stop` | 終了条件不成立後に後続 i を生成しない | 単調打ち切り |
| U-6 | `test_schedule_empty_raises` | 全期間 < is_span+oos_span（i=0 不成立） | `WalkForwardError`（context に span/step） |
| U-7 | `test_schedule_invalid_mode_raises` | mode 不正 | `WalkForwardError` |
| U-8 | `test_schedule_int_time_type` | `int` epoch 時刻＋int span | datetime64 と同一ロジックで決定論 |
| U-9 | `test_stitch_additive_sum` | 区分 A 総和（profit/trades 等） | Σ 一致 |
| U-10 | `test_stitch_recomputed_from_totals` | 区分 B が A 総和から再計算（窓別平均でない） | `Σgp/Σgl` 等一致 |
| U-11 | `test_stitch_recomputed_zero_denominator_none` | `Σgross_loss==0` 等 | recomputed=None |
| U-12 | `test_stitch_non_stitchable_per_window_only` | 区分 C が窓別系列のみ・通期スカラ非出力 | `additive`/`recomputed` に区分 C キー不在 |
| U-13 | `test_stitch_classification_covers_all_fields` | 3 分類が `dataclasses.fields(BacktestStats)` を網羅かつ互いに素 | 漏れ・重複 0 |
| U-14 | `test_stitch_empty_raises` | 空 oos_stats 列 | `WalkForwardError` |
| U-15 | `test_efficiency_profit_ratio_median_min` | profit ratio の中央値・最小値 | finite のみから算出 |
| U-16 | `test_efficiency_excludes_none_windows` | ratio None 窓を除外＋件数 | `excluded_none_count` 正・median は finite のみ |
| U-17 | `test_efficiency_all_none_returns_none` | 全窓 None | median/minimum=None・件数=W |
| U-18 | `test_cli_random_without_seed_exits_2` | random で `--seed` 未指定 | `SystemExit(2)`（parser.error・🟡-2） |
| U-19 | `test_cli_random_without_n_samples_exits_2` | random で `--n-samples` 未指定 | `SystemExit(2)` |
| U-20 | `test_cli_unknown_search_param_exits_2` | search_space に未知キー | `SystemExit(2)`（B-2） |
| U-21 | `test_cli_grid_no_seed_ok` | grid は seed 不要 | 入口検証通過 |
| U-22 | `test_keyword_whitelist_matches_build_interactor` | `_BUILD_INTERACTOR_KEYWORDS` が `build_interactor` 実シグネチャと一致 | `inspect.signature` 突合一致 |
| U-23 | `test_walk_forward_py_import_dependency` | `walk_forward.py` の ast 解析で禁止 import 不在 | `pandas`/`simulator.main`/`adapter`/`tools` 非 import |
| U-24 | `test_walk_forward_oos_optimize_error_raises_with_window_id` | 窓内 OptimizeError | `WalkForwardError`（context に window_index） |

U-22 は `inspect.signature(build_interactor).parameters` のキー集合と `_BUILD_INTERACTOR_KEYWORDS` の一致を検証し、`build_interactor` シグネチャ変更時に whitelist 乖離を機械検出する（B-2 の虚偽許容集合を防ぐ）。U-23 は `ast.parse(open("walk_forward.py").read())` を走査し `Import`/`ImportFrom` ノードのモジュール名が禁止集合に含まれないことを assert（依存方向・C-W4 の機械検証）。

### 8.2 結合テスト（WF→SP2→engine・縮退ケース）

配置：`simulator/tests/integration/test_walk_forward_integration.py`。読み取り専用 fixture を使用し mtime 不変を assert。

| # | テスト名 | 検証内容 | 期待 |
|---|---|---|---|
| I-1 | `test_single_window_single_candidate_matches_sp2` | 単一窓×単一候補（縮退）で WF 経由の窓別 oos_stats が SP2 `optimize` 直呼びの oos_stats と一致 | `BacktestStats` 全フィールド一致 |
| I-2 | `test_wf_reproduces_2026_04_precedent` | 先例 2026-04（SP1/SP2 で確立した既知 split）を WF 単一窓で再現 | 既存先例の IS/OOS 値と一致 |
| I-3 | `test_total_run_count_matches_port_contract` | `make_run_segment` 呼出回数 = `Σ_i theoretical_count_i + W`（H-1） | カウンタ突合一致（grid/random 両方） |
| I-4 | `test_b1_window_full_only_correct_split` | factory 全期間 full_bars を破棄し当窓 full のみ渡しても窓内 IS/OOS が正しく分割（B-1） | 窓別 IS/OOS バー数が境界と整合 |
| I-5 | `test_fixture_readonly_mtime_unchanged` | 実行前後で fixture mtime 不変 | `os.stat().st_mtime` 一致（NFR-WS1） |

I-1/I-2 は「単一窓×単一候補」に縮退させることで WF が SP2/SP1 と一致することを検証する（SP2 再利用の正当性・基本設計 §8.3 システムテスト方針）。`schedule_windows` で 1 窓のみ生成する global 境界（`global_end == oos_end_0`）を与え、同じ split を SP2 `optimize` に直接渡した結果と突合する。I-3 は `make_run_segment` をカウンタでラップし grid/random 両方で総 run = `Σ theoretical_count_i + W` を実測突合（H-1 Port 契約）。

### 8.3 回帰テスト（決定論・byte 同一）

配置：`simulator/tests/integration/test_walk_forward_determinism.py`。

| # | テスト名 | 検証内容 | 期待 |
|---|---|---|---|
| R-1 | `test_same_input_same_window_list` | 同一入力で `schedule_windows` 2 回 | 窓列が完全一致 |
| R-2 | `test_same_input_byte_identical_json` | 同一入力で WF を 2 回実行し `walk_forward.json` を比較 | byte 同一（NFR-WD1） |
| R-3 | `test_grid_deterministic_window_best_params` | grid で窓別 best_params 再現 | 2 回一致 |
| R-4 | `test_random_seeded_deterministic` | random（seed 固定）で窓別 best/連結再現 | 2 回一致 |

R-2 は MEMORY「bugfix-pair-with-regression-test」方針に沿い、🟡-2 解消（seed 固定 random の決定論）を回帰で固定する。

### 8.4 カバレッジ目標・自動化範囲まとめ

| 対象 | 行カバレッジ | 分岐カバレッジ | 自動化 |
|---|---|---|---|
| `walk_forward.py`（純関数 3 本＋本体） | 100%（純関数）/95%（本体） | 100%（純関数）/90%（本体） | pytest 完全自動（純関数は engine 不要） |
| `walk_forward_cli.py`（入口検証・整形） | 95% | 90% | pytest（入口検証は engine 不要・結合は fixture） |
| 結合（WF→SP2→engine） | — | — | 読み取り専用 fixture で自動 |

---

## 9. 完結性（SP1/SP2/WF 統合の全体像と後続接続点）

### 9.1 3 サブフェーズ統合の全体像

```
SP1 simple-split : 固定 1 組 split を IS/OOS で並列評価（run_is_oos）
   └─ 部品：slice_is_bars（半開区間 head-prefix）・DegradationReport（ratio/delta）
SP2 optimize     : 探索空間×目的関数で IS を探索し best を OOS 検証（1 窓・optimize）
   └─ 部品：ParameterSearchPort（Grid/Random）・ObjectivePort（PF/Net/Sharpe/Recovery）
WF walk-forward  : 窓を step ずつ前進させ各窓で SP2 を反復し OOS を連結（本書）
   └─ schedule_windows（窓生成）＋ optimize 反復（SP2 再利用）＋ stitch_oos（連結）
```

WF は SP1（区間スライス・劣化レポート）と SP2（窓内最適化）を**統合する最終段**であり、本書をもって IS/OOS 方法論レイヤ（`.doc/ISOOS_BROWSER_PLAN_WIP.md` アクター D/E）が完結する。

### 9.2 後続（プレゼン/ブラウザ UI）への接続点（参考）

- `walk_forward(...)` UC は `WalkForwardResult` を返す純粋関数であり、`.doc/ISOOS_BROWSER_PLAN_WIP.md` §5 Phase 2 の `POST /walkforward`（非同期＋ポーリング）は本 UC を**委譲先として再利用**する（CLI と HTTP で UC 共有・C-W2 維持）。
- `to_json_dict(result, request)` が返す dict は web 比較ビューの API レスポンス素材（窓別表・連結表・効率表）にそのまま転用可能。
- TBD-W1（トレード列連結エクイティ）は presenter/is_oos 設計で `BacktestResult` 全体返却の新 run_segment 変種（新規ファイル・C-W2 維持）として後段に委譲。

---

## 10. 絶対制約の遵守確認

| 制約 | 本書での遵守 |
|---|---|
| 既存データ非波及（C-W1・NFR-WS1） | `assert_safe_output_dir` 再利用・出力は新規 OUT のみ・fixture 読み取り専用（I-5 で mtime 不変検証） |
| committed/SP1/SP2 無改変（C-W2・NFR-WS2） | 新規 2 ファイルのみ・既存は import 再利用（編集 0 行・§6.3） |
| 技術スタック追加禁止（C-W3） | 標準ライブラリ（`statistics`/`dataclasses`/`typing`）＋既存 usecase のみ。pandas は tools 層に限定 |
| クリーンアーキ依存方向（C-W4） | `walk_forward.py` は domain＋SP1/SP2 usecase のみ依存（U-23 ast 検証）・pandas/main 非 import |
| 基本設計から逸脱しない | FR-W1..W9／B-1/B-2/C-1/C-2/H-1/M-1 を §11 トレーサビリティで全項目対応 |

---

## 11. トレーサビリティ（基本設計項目 → 本書詳細 → 実証行）

| 基本設計項目 | 本書詳細 | 実コード実証 |
|---|---|---|
| FR-W1/W2（窓スケジュール） | §3.4.1 `schedule_windows` | `run_is_oos.py:34-38`（半開区間 head-prefix） |
| FR-W3（窓内 optimize） | §3.4.4 step 3 | `optimize.py:91-98`（optimize シグネチャ） |
| FR-W4（OOS 連結） | §3.4.2 `stitch_oos` | `models.py:91-143`（BacktestStats 全フィールド） |
| FR-W6（WF 効率） | §3.4.3 `aggregate_efficiency` | `run_is_oos.py:106`（ratio None 条件）／`run_is_oos.py:75-79`（by_name） |
| B-1（二重 full_bars 調停） | §3.4.4／§4.1 注 | `optimize.py:110-111,187-188`（full_bars 母集合）／`run_is_oos_cli.py:45-47`（request.bars 上書き） |
| B-2（search_space キー制約） | §4.3 `_BUILD_INTERACTOR_KEYWORDS`／§4.2 入口検証 2 | `main/__init__.py:256-285`（受理キーワード・catch-all なし）／`optimize_cli.py:51`（`**params` 展開） |
| C-1（profit 固定＋None 除外） | §3.4.3／§3.3.5 | `optimize.py:53-60`（metric_names に profit）／`run_is_oos.py:106` |
| C-2（🟡-2 入口検証） | §4.2 入口検証 1 | `optimize_cli.py:59-66,192-193`（RandomSearch・default None）／`optimize_strategies.py:92,97,105`（min(None) TypeError・Random(None)） |
| H-1（theoretical_count Port 契約） | §3.4.4 step 2／§7.1 | `optimize_ports.py:23`（theoretical_count）／`optimize_strategies.py:69-71,89-92`（grid/random で実 run 数一致） |
| H-2/H-3（終了条件・端数） | §3.4.1 終了条件 | `run_is_oos.py:34-38`（半開区間規約） |
| M-1（連結 3 分類） | §3.4.2 3 分類表 | `models.py:96-142`（全フィールド実在）／`run_is_oos_cli.py:49`（run_segment は stats のみ＝連結エクイティ不可） |
| NFR-WS2（無改変） | §6.3 | 新規 2 ファイル・git diff 0 行 |

---

## 12. 自己レビュー・上流入力検証の参照

本詳細設計は以下のスキル成果物に基づく自己点検を経ている：
- `prompt-validation-workflow`：本書末尾の報告（親会話返却）に Pre-mortem／証拠先行／残存リスクを記載。
- `upstream-input-validation`：上流入力（基本設計 v0.2.0・SP1/SP2 実コード・絶対制約）の前提を実コード Read で実証採用（§11 トレーサビリティが証拠列）。
