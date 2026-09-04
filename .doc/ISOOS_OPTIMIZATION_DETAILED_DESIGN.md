# IS/OOS 最適化（Optimization）詳細設計書

## 1. 文書情報

- 作成日：2026-06-20
- バージョン：v1.0.0
- 作成者：system-internal-design エージェント
- 上位設計（唯一の正）：`/workspaces/app/.doc/ISOOS_OPTIMIZATION_BASIC_DESIGN.md` v0.2.0
- 参照（SP1 詳細・部品契約）：`/workspaces/app/.doc/ISOOS_SIMPLE_SPLIT_DETAILED_DESIGN.md` v1.0.0
- 対象システム種別：バッチ／オフライン分析ツール（committed バックテストエンジン上のオーケストレーション層）
- 本書の責務：基本設計 v0.2.0 を「誰が実装しても同じ結果になる決定論的水準」（クラス名・メソッドシグネチャ・主要処理フロー・テスト契約）へ落とし込む。
- 変更履歴：
  - v1.0.0 (2026-06-20) 初版。基本設計 v0.2.0 の確定済前提（C-1 非有限除外／High-1 CSV 再ロード正直記載／High-2 best_is_stats 保持／High-3 random 整数インデックス抽出／M-1 失敗候補捕捉／M-2 超過拒否単一動作／M-3 max_candidates 必須）を実装シグネチャへ具体化。

### 1.1 基本設計からの逸脱の有無

逸脱なし。本書は基本設計 v0.2.0 の確定事項を実装シグネチャへ具体化するのみであり、新規の設計判断を導入しない。設計判断を要した実装詳細（公開 API のフィールド型・例外クラス配置・関数分割）はすべて §10 の根拠表に代替案比較・定量評価とともに併記する。

### 1.2 実コードによる前提実証（本詳細設計の土台・証拠先行）

| 前提 | 実証箇所 | 確認内容 |
|---|---|---|
| SP1 `slice_is_bars(bars, split)` 純関数（head 切り） | `simulator/usecase/run_is_oos.py:26-39` | `bar.time < split` 保持・`break` 早期打切。副作用なし |
| SP1 `extract_metrics(stats, names)` | `run_is_oos.py:91-93` | `{n: float(getattr(stats, n))}`。ObjectivePort 既定実装が内部再利用可 |
| SP1 `build_degradation_report(is, oos, names)` | `run_is_oos.py:96-110` | ratio（IS==0 で None）・delta 両格納。IS best vs OOS 劣化 |
| SP1 `DegradationReport`/`MetricDegradation` DTO | `run_is_oos.py:58-79` | `OptimizeResult.degradation` の型として再利用可 |
| SP1 `run_segment` 型契約 | `run_is_oos.py:20-23` `RunSegment = Callable[[Any, Any], Any]` | 注入コールバックの型として継承 |
| SP1 `run_is_oos(...)` は IS/OOS 対称 2 回呼び | `run_is_oos.py:134-135` | 非対称な最適化ループに不適合＝呼ばない（課題-O2） |
| SP1 tools `make_run_segment(controller, request)` | `run_is_oos_cli.py:38-51` | `controller._interactor.execute` 経由（B-1）。閉包パターン |
| SP1 tools `assert_safe_output_dir` / `_FORBIDDEN_PREFIXES` | `run_is_oos_cli.py:31-35,67-83` | `marketdata`/`fixtures`/`confirmation` 配下・repo_root 外を `OutputGuardError` で拒否 |
| SP1 tools `normalize_time` / `OutputGuardError` | `run_is_oos_cli.py:27-28,54-64` | 時刻正規化・出力ガード例外（tools 層に pandas 閉込） |
| `build_interactor(...)` が探索対象 params を kwargs で受ける | `simulator/main/__init__.py:256-285` | `lot_size`/`stop_loss_points`/`take_profit_points`/`entry_offset_points`/`entry_type`/`ma_period`/`ma_method`/`slope_shift`/`slope_min_points`/`digits`/`stops_level` 等が build 引数の実体（課題-O1） |
| `build_interactor` が内部で CSV を毎回ロード | `main/__init__.py:335,344` `_load_dataframe`/`market_data.load` ＋ `339-342` コメント（1 回読み統合は committed IF 変更要＝範囲外） | 候補ごと build 再構築で CSV 再ロード N_cand+1 回（High-1） |
| `run_backtest` の例外翻訳は build 段階のみ | `main/__init__.py:434-439` `try: build_interactor(**meta) except ConfigError→2 / BacktestError→1` | run 中（execute）の例外には翻訳が掛からない。最適化は execute 直叩きのため UC/tools が捕捉必須（M-1） |
| `MarginCallError` は `BacktestError` の孫 | `domain/exceptions.py:84,92` `ExecutionError(BacktestError)` / `MarginCallError(ExecutionError)` | `except BacktestError` で `MarginCallError` も捕捉可（M-1） |
| `BacktestStats` の目的関数フィールド実在（float） | `models.py:97-103` `profit`/`gross_profit`/`gross_loss`/`profit_factor`/`recovery_factor`/`expected_payoff`/`sharpe_ratio` | PF/Net/Sharpe/Recovery objective の参照先。float ゆえ NaN/±inf を取り得る（C-1） |
| `OptimizeError` を committed `domain/exceptions.py` に置けない | `domain/exceptions.py:1-93`（committed・無改変対象 C2） | usecase 新規ファイルに定義する（committed 編集禁止のため） |

---

## 2. モジュール／クラス詳細設計

### 2.0 ファイル構成（新規追加のみ・committed/SP1 無改変）

| ファイル | 層 | 新規/既存 | 責務 |
|---|---|---|---|
| `simulator/usecase/optimize_ports.py` | usecase（新規） | 新規 | `ParameterSearchPort`／`ObjectivePort` の抽象 IF（`typing.Protocol`）。標準ライブラリのみ依存。`ports.py` は非編集。 |
| `simulator/usecase/optimize.py` | usecase（新規） | 新規 | オーケストレーション UC。`OptimizeRequest`/`TrialRecord`/`OptimizeResult` DTO・`OptimizeError`・`optimize(...)` 関数。domain・`usecase.models`・`usecase.run_is_oos`（部品）・`usecase.optimize_ports` のみ依存。 |
| `simulator/usecase/optimize_strategies.py` | usecase（新規） | 新規 | 既定実装＝`GridSearch`/`RandomSearch`（`ParameterSearchPort`）・`PfObjective`/`NetProfitObjective`/`SharpeObjective`/`RecoveryObjective`（`ObjectivePort`）。`optimize_ports`・`run_is_oos.extract_metrics` のみ依存。 |
| `simulator/tools/optimize_cli.py` | tools（新規） | 新規 | 実行入口。CLI 引数解釈・読み取り専用ロード・`make_run_segment_factory`（params→run_segment ファクトリ）構成・`optimize` 呼出・出力先検証・JSON/Markdown 整形・新規 OUT 書込。 |
| `simulator/tests/unit/test_grid_search.py` | test（新規） | 新規 | GridSearch 辞書順候補列挙・max_candidates 超過拒否。 |
| `simulator/tests/unit/test_random_search.py` | test（新規） | 新規 | RandomSearch seed 固定再現・整数インデックス抽出・n_samples>N_space 全件。 |
| `simulator/tests/unit/test_objective_ports.py` | test（新規） | 新規 | PF/Net/Sharpe/Recovery score・非有限除外・argmax tie 先勝ち。 |
| `simulator/tests/unit/test_optimize_core.py` | test（新規） | 新規 | 失敗候補除外・best0件 OptimizeError・run 回数 N_cand+1・best_is_stats 再 run なし。 |
| `simulator/tests/unit/test_optimize_dependency.py` | test（新規） | 新規 | `import ast` で `optimize.py`/`optimize_ports.py` の禁止 import 不在を assert。 |
| `simulator/tests/integration/test_optimize_sp1_degenerate.py` | test（新規） | 新規 | 単一候補空間で SP1 結合（先例 2026-04・IS net+11370/5224・OOS net-4020/2438）と一致＋mtime 不変＋決定論 byte 同一。 |

committed（`simulator/domain`・既存 `simulator/usecase`：`run_is_oos.py` 含む・既存 `simulator/adapter`・`simulator/main`・`ports.py`）への差分は 0 行（NFR-OS2／C2）。

### 2.1 `simulator/usecase/optimize_ports.py`（Port 抽象）

#### 2.1.1 モジュール先頭ポリシー

```python
"""UC Port 抽象: 探索アルゴリズム／目的関数の差し替え IF（基本設計 §6.1・FR-O2/O3）。

ParameterSearchPort（探索戦略）と ObjectivePort（目的関数）を typing.Protocol で
定義する。標準ライブラリのみ依存（pandas/main/adapter/domain も import しない）。
committed ports.py は編集しない（C2）。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

# ParamSet: build_interactor へ **params でマージ可能な部分写像（不変ビュー）。
#   キーは build_interactor キーワード引数名（lot_size 等）。値は単一スカラ。
ParamSet = "Mapping[str, Any]"
```

- **`Protocol` 採用根拠**：基本設計 §3.3 が「標準 `typing.Protocol`／`abc.ABC` で表現可（依存追加なし）」と確定。`Protocol` は構造的部分型でダックタイピングを型レベルで担保し、既定実装が `optimize_ports` を import せずとも準拠可能（疎結合・§10 判断 1）。テスト用の任意 callable 実装も受け入れ可能。
- **`ParamSet` を `Mapping` とする根拠**：build_interactor へ `**params` でマージするため `Mapping[str, Any]`。順序保持（候補復号の決定論）は呼出側 dict（Python3.7+ 挿入順保持）で担保。

#### 2.1.2 `ParameterSearchPort`（探索戦略の抽象）

```python
@runtime_checkable
class ParameterSearchPort(Protocol):
    """探索アルゴリズムの抽象（FR-O2・差替可能・OCP）。

    search_space を決定論的順序の候補 ParamSet 列に展開する。grid/random を実装で切替。
    """

    def candidates(self, search_space: "Mapping[str, list]") -> "Iterable[ParamSet]":
        """探索空間→候補 ParamSet の決定論的順序付きイテラブル（基本設計 FO-02）。

        引数:
          search_space: 可変パラメータ名 -> 有限候補値リスト の写像。
                        grid/random とも同一の離散候補集合空間。
        後条件:
          辞書順序規約（§2.1.4）に従う決定論的順序の ParamSet 列。
        注意:
          理論候補数 > max_candidates の上限判定は本メソッドが行う（FO-02・M-2）。
          超過時は OptimizeError（理由=候補数超過・件数を context へ）を送出する。
        """
        ...

    def theoretical_count(self, search_space: "Mapping[str, list]") -> int:
        """列挙前に算出する理論候補数（grid=N_space / random=min(n_samples,N_space)）。

        上限判定（FR-O9・NFR-OP2）に UC が用いる。列挙コストを払わず件数を返す。
        """
        ...
```

- **`theoretical_count` を分離する根拠**：基本設計 NFR-OP2「列挙前に理論候補数を算出し max_candidates と照合」を満たすため、候補を実体化する前に件数のみを O(1)〜O(キー数) で得る IF を切る。これにより「列挙してから件数判定」（巨大 grid のメモリ膨張）を回避（§10 判断 2）。

#### 2.1.3 `ObjectivePort`（目的関数の抽象）

```python
@runtime_checkable
class ObjectivePort(Protocol):
    """目的関数の抽象（FR-O3・差替可能・OCP）。

    IS の BacktestStats から「大きいほど良い」単一スカラを返す（argmax 規約）。
    返値の有限性判定（math.isfinite）は UC 側で行う（C-1）。本 Port は値を返すのみ。
    """

    name: str  # 目的関数名（ログ／レポート出力用。例 "profit_factor"）

    def score(self, stats: Any) -> float:
        """IS BacktestStats -> 目的値（float・大きいほど良い）。"""
        ...
```

- **有限性判定を Port 外に置く根拠**：基本設計 FO-04 が「`score` が返す値の有限性は `math.isfinite(score)` で判定することを規約化」と確定。判定責務を UC（§2.3.5）へ集約し、各 Objective 実装は単純にフィールドを返すのみ（DRY・除外件数ログの一元化）。

### 2.2 `simulator/usecase/optimize_strategies.py`（既定実装）

#### 2.2.1 モジュール先頭ポリシー

```python
"""UC Port 既定実装: GridSearch/RandomSearch・PF/Net/Sharpe/Recovery Objective。

optimize_ports の Protocol を満たす具体実装（OCP：新アルゴリズム追加で既存無改変）。
標準ライブラリ（itertools/random/math）＋ usecase.run_is_oos.extract_metrics のみ依存。
pandas/main/adapter は import しない（クリーンアーキ依存方向）。
"""
from __future__ import annotations

import itertools
import random
from typing import Any, Iterable, Mapping

from simulator.usecase.optimize import OptimizeError  # 上限超過の明示中断（§2.3.2）
```

- **`OptimizeError` を `optimize.py` から import する根拠**：基本設計は `OptimizeError` を「usecase 新規ファイルに定義」（committed `domain/exceptions.py` 無改変・C2）と確定。`optimize.py` を一次定義箇所とし、戦略実装はそれを参照する（型の単一所在・循環回避は §10 判断 3 で評価）。

#### 2.2.2 辞書順序規約（決定論の基礎・NFR-OD1）

全 `ParameterSearchPort` 実装が従う共通規約（基本設計 FO-02）：

1. **キー順固定**：`keys = sorted(search_space.keys())`（辞書順昇順）。
2. **値リスト順固定**：各キーの候補値リストは入力された順序をそのまま採用（呼出側が決定する。GridSearch/RandomSearch は再ソートしない）。
3. **基準インデックス列**：`keys` 順に各値リストを並べた全直積を辞書順（左キーほど上位）で列挙したインデックス列 `range(N_space)` を基準順序とする（`N_space = Π len(search_space[k])`）。
4. **インデックス→ParamSet 復号**：基準インデックス `idx` を、`keys` の右端を最下位桁とする混合基数で各キーの値リスト添字へ分解し、`{k: search_space[k][i_k] for k in keys}`（挿入順は `keys` 順）を構築。

```python
def _ordered_keys(search_space: "Mapping[str, list]") -> "list[str]":
    return sorted(search_space.keys())


def _space_size(search_space: "Mapping[str, list]", keys: "list[str]") -> int:
    n = 1
    for k in keys:
        n *= len(search_space[k])
    return n


def _decode_index(idx: int, search_space: "Mapping[str, list]", keys: "list[str]") -> dict:
    """基準インデックス -> ParamSet（keys を右端最下位桁とする混合基数復号・決定論）。"""
    out: dict = {}
    rem = idx
    for k in reversed(keys):           # 右端キーが最下位桁
        size = len(search_space[k])
        out[k] = search_space[k][rem % size]
        rem //= size
    return {k: out[k] for k in keys}   # 挿入順を keys 昇順へ整える
```

- **`itertools.product` と `_decode_index` の同値性**：`itertools.product(*[search_space[k] for k in keys])` が返すタプル列は「右端が最も速く回る」辞書順であり、`i` 番目が `_decode_index(i, ...)` と一致する。GridSearch は `itertools.product` を直接使い、RandomSearch は整数インデックス抽出後に `_decode_index` で復号する（両者が同一基準順序を共有することを単体テストで固定＝§6.2）。

#### 2.2.3 `GridSearch`

```python
class GridSearch:
    """直積全列挙（辞書順）。N_cand = N_space（基本設計 FO-02 grid）。"""

    def __init__(self, *, max_candidates: int) -> None:
        # max_candidates は必須（既定なし・M-3）。__init__ で受領し candidates で判定。
        self.max_candidates = max_candidates

    def theoretical_count(self, search_space: "Mapping[str, list]") -> int:
        keys = _ordered_keys(search_space)
        return _space_size(search_space, keys)

    def candidates(self, search_space: "Mapping[str, list]") -> "Iterable[dict]":
        keys = _ordered_keys(search_space)
        n_space = _space_size(search_space, keys)
        if n_space > self.max_candidates:                          # M-2 単一動作：拒否
            raise OptimizeError(
                "theoretical candidate count exceeds max_candidates",
                context={"theoretical": n_space, "max_candidates": self.max_candidates,
                         "algo": "grid"},
            )
        for combo in itertools.product(*[search_space[k] for k in keys]):
            yield {k: v for k, v in zip(keys, combo)}              # 辞書順・挿入順=keys
```

- **`yield`（ジェネレータ）採用根拠**：基本設計 FO-02 後条件は「決定論的順序付きリスト」。`yield` で遅延列挙し UC 側がループ消費する（巨大 grid のメモリ一括確保回避）。ただし上限判定は列挙前に `n_space` で即時実施するため、超過時は 1 件も yield せず拒否（§10 判断 2）。

#### 2.2.4 `RandomSearch`

```python
class RandomSearch:
    """離散候補集合からの整数インデックス非復元抽出（基本設計 FO-02 random・High-3）。"""

    def __init__(self, *, seed: int, n_samples: int, max_candidates: int) -> None:
        self.seed = seed
        self.n_samples = n_samples
        self.max_candidates = max_candidates                      # M-3 必須

    def theoretical_count(self, search_space: "Mapping[str, list]") -> int:
        keys = _ordered_keys(search_space)
        n_space = _space_size(search_space, keys)
        return min(self.n_samples, n_space)                       # 理論候補数（M-2）

    def candidates(self, search_space: "Mapping[str, list]") -> "Iterable[dict]":
        keys = _ordered_keys(search_space)
        n_space = _space_size(search_space, keys)
        k = min(self.n_samples, n_space)                          # n_samples>N_space は全件
        if k > self.max_candidates:                               # M-2 単一動作：拒否
            raise OptimizeError(
                "theoretical candidate count exceeds max_candidates",
                context={"theoretical": k, "max_candidates": self.max_candidates,
                         "algo": "random", "n_space": n_space, "n_samples": self.n_samples},
            )
        rng = random.Random(self.seed)                            # seed 固定で決定論
        idxs = rng.sample(range(n_space), k=k)                    # 整数インデックス非復元抽出
        for idx in sorted(idxs):                                  # 選択インデックスの昇順で復号
            yield _decode_index(idx, search_space, keys)
```

- **`random.Random(seed).sample(range(N_space), k)` 採用根拠**：基本設計 High-3 が確定方式。float 値そのものの等価比較に依存せず整数インデックスで非復元抽出するため、float 等価比較に起因する重複判定の不安定を回避（§10 判断 4）。
- **`sorted(idxs)` で昇順列挙する根拠**：基本設計 FO-02 後条件「random は seed 固定で選ばれたインデックスの昇順」。`sample` の返却順ではなく**選択集合をインデックス昇順**に並べて列挙＝tie-break（FO-05 先勝ち）が「基準順序＝インデックス昇順」で一意化される。`sample` 自体が seed 固定で決定論的な集合を返すため、昇順整列で順序まで完全決定論化。
- **`n_samples > N_space` の全件採用**：`k = min(n_samples, N_space)` により全件（N_space 件）。基本設計 FO-02 の「全件採用＋ログ明示」を満たす。ログ明示は UC 側で `theoretical_count` と実際の候補数の差を記録（§2.3.4）。

#### 2.2.5 Objective 既定実装（PF/Net/Sharpe/Recovery）

```python
class _FieldObjective:
    """BacktestStats の単一フィールドを score とする基底（大きいほど良い・FO-04）。"""

    def __init__(self, name: str, field: str) -> None:
        self.name = name
        self._field = field

    def score(self, stats: Any) -> float:
        # extract_metrics（SP1・run_is_oos.py:91）を 1 フィールドに適用し float 化。
        from simulator.usecase.run_is_oos import extract_metrics
        return extract_metrics(stats, (self._field,))[self._field]


class PfObjective(_FieldObjective):
    def __init__(self) -> None:
        super().__init__("profit_factor", "profit_factor")


class NetProfitObjective(_FieldObjective):
    def __init__(self) -> None:
        super().__init__("profit", "profit")


class SharpeObjective(_FieldObjective):
    def __init__(self) -> None:
        super().__init__("sharpe_ratio", "sharpe_ratio")


class RecoveryObjective(_FieldObjective):
    def __init__(self) -> None:
        super().__init__("recovery_factor", "recovery_factor")
```

- **`extract_metrics`（SP1）再利用根拠**：基本設計 FO-04 が「SP1 の `extract_metrics` を内部利用可」と確定。`getattr`＋`float()` 化を SP1 純関数に委譲し DRY を満たす。`profit_factor`/`sharpe_ratio`/`recovery_factor` は float ゆえ NaN/±inf を返し得る（`models.py:100-103`）が、有限性判定は UC（§2.3.5）が `math.isfinite` で行うため Objective は値をそのまま返す（C-1 の責務分離）。
- **「小さいほど良い」指標の符号反転は既定外**（基本設計 TBD-O6・YAGNI）。既定 4 種はいずれも大きいほど良いため符号反転機構を実装しない。将来追加は `ObjectivePort` 実装側で符号反転して「大きいほど良い」に正規化（OCP・既存無改変）。

### 2.3 `simulator/usecase/optimize.py`（オーケストレーション UC）

#### 2.3.1 モジュール先頭ポリシー（committed 規約の継承）

```python
"""UC: IS/OOS 最適化オーケストレーション（基本設計 v0.2.0 / FR-O1..O9）。

探索空間×目的関数で IS を探索し best params を確定、best params を凍結して OOS で
検証し劣化レポートを返す。エンジン実行手段は make_run_segment ファクトリ（params->
run_segment）として tools 層から注入する（DIP・課題-O1）。

usecase は domain のみ依存（adapter/framework/main・pandas を import しない）。
SP1 run_is_oos の純関数（slice_is_bars/build_degradation_report/extract_metrics）と
DegradationReport を部品再利用するが、run_is_oos 関数は呼ばない（IS/OOS 対称契約＝
非対称な最適化ループに不適合・課題-O2）。SP1 は無改変（C2）。
時刻は domain と同じく numpy.datetime64 | int（pd.Timestamp を前提にしない）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from simulator.usecase.optimize_ports import ObjectivePort, ParameterSearchPort, ParamSet
from simulator.usecase.run_is_oos import (
    DegradationReport,
    RunSegment,
    build_degradation_report,
    slice_is_bars,
)
```

- 禁止 import（依存方向検証＝§6.2.5 で `import ast` により不在を assert）：`pandas`・`simulator.main`・`simulator.adapter`・`simulator.tools`。`DegradationReport`/`RunSegment`/`build_degradation_report`/`slice_is_bars` は SP1 usecase 内・同階層のため import 可。`BacktestStats` 型は疎結合のため `Any` で受ける（SP1 と同規約・`models.py:13`）。

#### 2.3.2 `OptimizeError`（型配置の確定）

```python
class OptimizeError(Exception):
    """最適化が結果を出せない場合の明示中断（無音禁止・C-1/M-1/M-2）。

    送出条件（いずれも基本設計確定）:
      (a) 理論候補数 > max_candidates（ParameterSearchPort.candidates 内・M-2）
      (b) 有効候補 0 件（全候補が非有限スコアまたは失敗・C-1/M-1）

    context（dict）に中断理由の内訳を載せる:
      total_candidates / excluded_nonfinite / excluded_failed / finite_candidates 等。
    """

    def __init__(self, message: str, *, context: "dict | None" = None) -> None:
        super().__init__(message)
        self.context = context or {}
```

- **`domain/exceptions.py` に置かない根拠（C2）**：`OptimizeError` を committed `domain/exceptions.py`（§1.2 実証・無改変対象）に追加すると committed 差分が生じ C2 違反。よって usecase 新規ファイル `optimize.py` に定義する（基本設計 §5.2 OptimizeError 概念・「committed `domain/exceptions.py` を編集しないため usecase 新規ファイルに定義」を実装で満たす）。
- **`BacktestError` を継承しない根拠**：`OptimizeError` はオーケストレーション層の制御中断であり、エンジンの `BacktestError` 系（候補ごとに捕捉対象＝§2.3.4）とは意味も捕捉境界も異なる。`Exception` 直系とし、`BacktestError` 捕捉ロジックが誤って `OptimizeError` を握り潰さないことを保証（§10 判断 5）。`context` 属性は domain `BacktestError`（`exceptions.py:46`）と同じ診断 dict 規約を踏襲。

#### 2.3.3 DTO 定義

```python
@dataclass(frozen=True)
class OptimizeRequest:
    """最適化の入力（基本設計 §5.2・§6.1）。

    エンジン構築パラメータ（base_kwargs）と探索空間値は make_run_segment ファクトリ側
    （tools 層）に閉じるため本 Request は持たない。本 Request は探索統括に UC が必要と
    する最小集合に限定する。
    """
    search_space: "Mapping[str, list]"   # 可変パラメータ名 -> 有限候補値リスト
    split: Any                           # 分割境界（bar.time と比較可能・必須）
    is_trading_start: Any                # IS 取引開始境界（必須・SP1 H-1 継承）
    metric_names: "tuple[str, ...]" = (
        "profit", "profit_factor", "recovery_factor",
        "expected_payoff", "sharpe_ratio", "trades",
    )
```

```python
@dataclass
class TrialRecord:
    """探索 1 試行の記録（基本設計 §5.2・High-2/C-1/M-1）。"""
    params: "ParamSet"                   # 1 候補のパラメータ組
    is_score: "float | None"             # ObjectivePort.score。失敗候補は None
    is_finite: bool                      # math.isfinite(is_score)（C-1）。失敗は False
    failed: bool                         # 候補実行が例外で失敗（M-1）
    failure_reason: "str | None"         # 例外型名＋メッセージ（失敗時のみ）
    is_stats: Any                        # IS run の BacktestStats（保持・High-2）。失敗は None
    is_best: bool = False                # best に選ばれたか（出力後に確定）
```

```python
@dataclass
class OptimizeResult:
    """最適化の出力一式（基本設計 §5.2・§6.1）。"""
    best_params: "ParamSet"
    best_is_stats: Any                   # best 候補の IS run 結果（保持値・再 run なし・High-2）
    best_is_score: float
    oos_stats: Any                       # best params で OOS run（別 build 1 回）
    degradation: DegradationReport       # build_degradation_report(best_is_stats, oos_stats)
    trials: "list[TrialRecord]"          # 全試行ログ（FR-O8）
    excluded_count: "dict[str, int]"     # 除外内訳 {"nonfinite": int, "failed": int}（C-1/M-1）
    total_candidates: int                # 列挙された候補総数
    finite_candidates: int               # argmax 母集合（有限・非失敗）件数
```

- **`OptimizeRequest` を `frozen=True` とする根拠**：入力一式の不変性（探索中に書き換わらない＝決定論担保）。`TrialRecord`/`OptimizeResult` は構築途中で `is_best` 等を更新するため非 frozen。
- **`excluded_count` を dict 内訳とする根拠**：基本設計 §5.2「excluded_count（非有限スコア／失敗で除外した候補数）」と FO-04/M-1 が非有限と失敗を別理由で除外すると確定。内訳 `{"nonfinite", "failed"}` で両者を分離記録（ログ・レポートに件数明示・FR-O9）。
- **`best_is_stats` は保持値（High-2）**：探索ループ中に `TrialRecord.is_stats` へ保持した best 候補の IS 結果をそのまま採用。best の IS を再 run しない（total run = N_cand+1 厳守）。

#### 2.3.4 make_run_segment ファクトリ契約（候補ごと生成方式・課題-O1）

```python
# 型エイリアス（ドキュメント用）。params -> その params の 1 区間実行 run_segment を返す。
#   tools 層が build_interactor(**base_kwargs, **params) を再構築して閉包を生成（課題-O1）。
MakeRunSegment = "Callable[[ParamSet], RunSegment]"
```

- **契約**：`make_run_segment(params) -> run_segment`。UC は候補 params ごとにこれを呼んで run_segment を得る。tools 層実装（§2.4）が `build_interactor(**base_kwargs, **params)` を再構築し、SP1 `make_run_segment(controller, request)`（`run_is_oos_cli.py:38`）相当の閉包（`controller._interactor.execute` 経由・B-1）を構成する。
- **UC が main 非依存である根拠**：UC は `make_run_segment` を `Callable` として受けるのみで `build_interactor` を import しない。依存方向は tools→usecase（内向き・DIP）。これにより課題-O1（候補ごと build 再構築）を tools へ閉じ、UC を domain/同階層依存に保つ。

#### 2.3.5 公開関数 `optimize`

```python
def optimize(
    *,
    request: OptimizeRequest,
    full_bars: Any,
    make_run_segment: "MakeRunSegment",
    search_port: ParameterSearchPort,
    objective_port: ObjectivePort,
) -> OptimizeResult:
    """IS 探索→best 確定→OOS 検証を 1 回行う（FR-O1..O9）。

    引数:
      request          : OptimizeRequest（search_space・split・is_trading_start・metric_names）
      full_bars        : 全期間バー列（読み取り専用・IS slice の入力／OOS run 入力）
      make_run_segment : params -> run_segment ファクトリ（課題-O1・tools 注入・DIP）
      search_port      : ParameterSearchPort（grid/random・候補列挙＋上限判定 M-2）
      objective_port   : ObjectivePort（PF/Net/Sharpe/Recovery・score）

    処理:
      (1) full_bars を list 実体化し IS bars を slice_is_bars で 1 回導出（全候補共通）。
          IS/OOS 空区間検証を探索ループ前に 1 回（SP1 継承・候補非依存）。
      (2) candidates = search_port.candidates(search_space)（決定論順・上限超過は OptimizeError）。
      (3) for params in candidates:  # N_cand 回
            rs = make_run_segment(params)
            try: is_stats = rs(is_bars, is_trading_start)
            except (ConfigError|BacktestError|MarginCallError): failed 記録し continue（M-1）
            score = objective_port.score(is_stats)
            is_finite = math.isfinite(score)（C-1）
            TrialRecord 追記（is_stats 保持・High-2）
      (4) finite = [t for t in trials if t.is_finite and not t.failed]
          if not finite: OptimizeError（best 0 件・無音禁止・C-1/M-1）
          best = argmax(finite by is_score; tie=列挙順先勝ち)
          best_is_stats = best.is_stats（保持値・再 run しない・High-2）
      (5) rs_best = make_run_segment(best.params)  # OOS 用別 build（N_cand+1 番目の run）
          oos_stats = rs_best(full_list, split)    # OOS: full+trading_start=split
      (6) degradation = build_degradation_report(best_is_stats, oos_stats, metric_names)（SP1）
      (7) OptimizeResult 構築

    戻り値: OptimizeResult
    例外  : OptimizeError（上限超過 / 有効候補 0 件）。
            候補ごとの ConfigError/BacktestError/MarginCallError は捕捉・除外・継続（M-1）。
    """
```

実装フロー（決定論・参照実装）：

```python
    from simulator.domain.exceptions import (
        BacktestError, ConfigError, MarginCallError,
    )  # 関数内 import: usecase→domain（内向き・許容）。MarginCallError は BacktestError の孫だが
       # 可読性のため明示列挙（捕捉対象を IF として宣言・M-1）。

    full_list = list(full_bars)
    is_bars = slice_is_bars(full_list, request.split)

    # (1) 空区間検証（探索ループ前に 1 回・split/is_trading_start は全候補共通）
    oos_count = sum(1 for b in full_list if b.time >= request.split)
    if len(is_bars) < 1:
        raise OptimizeError("IS 区間が空（bar.time < split を満たすバーが 0 件）",
                            context={"phase": "pre_validation"})
    if oos_count < 1:
        raise OptimizeError("OOS 区間が空（bar.time >= split を満たすバーが 0 件）",
                            context={"phase": "pre_validation"})
    if not (request.is_trading_start <= request.split):
        raise OptimizeError("is_trading_start は split 以下である必要がある",
                            context={"phase": "pre_validation"})

    # (2)(3) 候補列挙＋IS run ループ（上限超過は candidates が OptimizeError を送出）
    trials: "list[TrialRecord]" = []
    for params in search_port.candidates(request.search_space):
        params = dict(params)  # 防御的コピー（外部 dict の変異を持ち込まない）
        rs = make_run_segment(params)
        try:
            is_stats = rs(is_bars, request.is_trading_start)
        except (ConfigError, BacktestError, MarginCallError) as exc:  # M-1: execute 直叩き捕捉
            trials.append(TrialRecord(
                params=params, is_score=None, is_finite=False, failed=True,
                failure_reason=f"{type(exc).__name__}: {exc}", is_stats=None,
            ))
            continue
        score = objective_port.score(is_stats)
        is_finite = math.isfinite(score)  # C-1: 非有限は argmax 母集合外
        trials.append(TrialRecord(
            params=params, is_score=score, is_finite=is_finite, failed=False,
            failure_reason=None, is_stats=is_stats,  # High-2: 保持
        ))

    total = len(trials)
    excluded_failed = sum(1 for t in trials if t.failed)
    excluded_nonfinite = sum(1 for t in trials if (not t.failed) and (not t.is_finite))

    # (4) 有限・非失敗の母集合で argmax（tie=列挙順先勝ち）
    finite = [t for t in trials if t.is_finite and not t.failed]
    if not finite:
        raise OptimizeError(
            "有効候補 0 件（全候補が非有限スコアまたは失敗）",
            context={"total_candidates": total,
                     "excluded_nonfinite": excluded_nonfinite,
                     "excluded_failed": excluded_failed,
                     "finite_candidates": 0},
        )
    best = finite[0]
    for t in finite[1:]:
        if t.is_score > best.is_score:  # 厳密 > のみ更新＝tie は先出（列挙順先勝ち）
            best = t
    best.is_best = True
    best_is_stats = best.is_stats  # High-2: 保持値を採用（再 run しない）

    # (5) OOS run（best params で別 build 1 回＝N_cand+1 番目の run）
    rs_best = make_run_segment(best.params)
    oos_stats = rs_best(full_list, request.split)  # OOS: full+trading_start=split

    # (6) 劣化算出（SP1 再利用）
    degradation = build_degradation_report(
        best_is_stats, oos_stats, request.metric_names
    )

    # (7) 結果構築
    return OptimizeResult(
        best_params=best.params, best_is_stats=best_is_stats,
        best_is_score=best.is_score, oos_stats=oos_stats, degradation=degradation,
        trials=trials,
        excluded_count={"nonfinite": excluded_nonfinite, "failed": excluded_failed},
        total_candidates=total,
        finite_candidates=len(finite),
    )
```

- **argmax を `>` 厳密更新ループで実装する根拠（tie 先勝ち・NFR-OD1）**：`max(..., key=...)` は同値時に「最初の最大要素」を返すが、可読性と「列挙順先勝ち」契約の明示のため明示ループで `>` のみ更新する。`finite` は `candidates` の決定論順（grid 辞書順／random インデックス昇順）を保持するため、tie は基準順序の先出が勝つ（FO-05 確定）。
- **`MarginCallError` を `BacktestError` と並記する根拠（M-1）**：`MarginCallError` は `BacktestError` の孫（`exceptions.py:84,92`）で `except BacktestError` でも捕捉されるが、基本設計 M-1 が「`execute` 直叩きのため `run_backtest` の翻訳が掛からず UC/tools が明示捕捉する必要がある」と確定。捕捉対象を IF として明示宣言するため 3 型を並記（重複捕捉だが意図の文書化＝§10 判断 6）。
- **`from simulator.domain.exceptions import ...` が依存方向に整合する根拠**：usecase→domain は内向き依存で許容（クリーンアーキ）。SP1 `run_is_oos.py` も domain を import 可（§1.2 規約）。禁止は usecase→main/adapter/pandas のみ（§6.2.5 で assert）。
- **空区間検証を `OptimizeError` で送出する根拠**：SP1 は `IsOosValidationError` を使うが、最適化は SP1 `run_is_oos` 関数を呼ばない（部品のみ再利用）ため `IsOosValidationError` は発生しない。検証 NG は最適化の明示中断として `OptimizeError`（`context.phase="pre_validation"`）で統一する（§10 判断 7）。

### 2.4 `simulator/tools/optimize_cli.py`（実行入口）

#### 2.4.1 責務とモジュールポリシー

責務（SP1 `run_is_oos_cli.py` の Composition Root 利用側パターンを params 可変へ拡張）：

1. CLI 引数解釈（`argparse`）。探索空間（パラメータ名→候補値リスト）・探索アルゴリズム（grid/random+seed+n_samples）・目的関数（pf/net/sharpe/recovery）・`max_candidates`（必須）・split・is_trading_start・base_kwargs・out-dir。
2. `build_interactor(**base_kwargs)` を 1 回構築し `request.bars` を `full_bars`／`sample_time` 取得用に得る（時刻正規化のサンプル）。
3. `make_run_segment_factory(base_kwargs)` を構成（params→run_segment ファクトリ・課題-O1）。
4. `search_port`／`objective_port` の具体実装を選択・注入。
5. `optimize(request=..., full_bars=..., make_run_segment=..., search_port=..., objective_port=...)` を呼ぶ。
6. 出力先検証（SP1 `assert_safe_output_dir` 再利用）→ JSON/Markdown 整形 → 新規 OUT 書込。

`pandas`・`simulator.main` の import は tools 層では許容（Composition Root 利用側・SP1 と同層境界）。

#### 2.4.2 params→run_segment ファクトリ（課題-O1 の tools 層解決・参照実装）

```python
def make_run_segment_factory(
    base_kwargs: dict,
    *,
    split_str: str, is_trading_start_str: str,
) -> "tuple[Callable[[Mapping], Callable[[Any, Any], Any]], Any, Any, Any]":
    """base_kwargs を閉包し params -> run_segment を返すファクトリを構成（課題-O1）。

    候補 params ごとに build_interactor(**base_kwargs, **params) を再構築し（CSV 再ロード
    込・High-1）、SP1 make_run_segment(controller, request) 相当の閉包で run_segment を生成。

    戻り値:
      factory          : params -> run_segment（UC へ注入）
      full_bars        : base_kwargs 単独 build の request.bars（IS slice の入力）
      split            : normalize_time(split_str, sample_time)
      is_trading_start : normalize_time(is_trading_start_str, sample_time)
    """
    from simulator.main import build_interactor
    from simulator.tools.run_is_oos_cli import make_run_segment, normalize_time

    # base build（full_bars と時刻正規化サンプルの取得・1 回）。
    base_controller, base_request = build_interactor(**base_kwargs)
    full_bars = base_request.bars
    sample_time = full_bars[0].time
    split = normalize_time(split_str, sample_time)
    is_trading_start = normalize_time(is_trading_start_str, sample_time)

    def factory(params: "Mapping[str, Any]") -> "Callable[[Any, Any], Any]":
        controller, request = build_interactor(**{**base_kwargs, **params})  # 候補ごと再構築
        return make_run_segment(controller, request)  # SP1 閉包（execute 直叩き・B-1）

    return factory, full_bars, split, is_trading_start
```

- **SP1 `make_run_segment`／`normalize_time` を再利用する根拠**：`run_is_oos_cli.py:38,54` の確立済関数（execute 直叩き B-1・時刻正規化）をそのまま import 利用し DRY を満たす。SP1 ファイルは無改変（読み取り import のみ・C2）。
- **base build を 1 回行う根拠**：`full_bars`（IS slice 入力）と時刻正規化サンプルの取得に base_kwargs 単独 build を 1 回使う。これは「N_cand+1 run」の run には**含まない**（execute を呼ばず bars 取得のみ）。CSV ロード回数は base 1 回＋候補ごと N_cand 回＋OOS 用 best 再 build 1 回（High-1・NFR-OP4）。
- **`{**base_kwargs, **params}` のマージ順**：params が base_kwargs の同名キーを上書きする（探索対象パラメータが base の既定値を上書き）。

#### 2.4.3 CLI 引数

| 引数 | 型 | 必須 | 説明 |
|---|---|---|---|
| `--data-path` `--ea-name` `--symbol` `--period` ほか build_interactor 透過引数 | — | 一部必須 | base_kwargs を構成（SP1 `_build_arg_parser` 踏襲） |
| `--split` | str | 必須 | 分割境界（ISO 日時） |
| `--is-trading-start` | str | 必須 | IS 取引開始（H-1） |
| `--search-param` | str（`name=v1,v2,..` 反復） | 必須（1 件以上） | 探索空間 1 軸（パラメータ名→候補値リスト） |
| `--search-algo` | str（`grid`/`random`） | 必須 | 探索アルゴリズム選択 |
| `--seed` | int | random 時必須 | RandomSearch seed（High-3） |
| `--n-samples` | int | random 時必須 | RandomSearch サンプル数 |
| `--max-candidates` | int | **必須**（既定なし・M-3） | 理論候補数上限。未指定は argparse エラー |
| `--objective` | str（`pf`/`net`/`sharpe`/`recovery`） | 必須 | 目的関数選択 |
| `--out-dir` | str | 必須 | 新規出力先（`assert_safe_output_dir` 検証） |

- `--max-candidates` を `required=True` とすることで M-3（必須・既定なし）を argparse レベルで構造的に担保（未指定は SystemExit 2）。
- `--search-param` の値リストは型推論（int/float/bool/str）を SP1 `_parse_config_overrides`（`run_is_oos_cli.py:145`）と同方針で行う。値リストの順序は CLI 入力順を保持（辞書順序規約の「値リスト順は入力順」§2.2.2）。

#### 2.4.4 Port 実装の選択・注入

```python
def _build_search_port(args) -> Any:
    from simulator.usecase.optimize_strategies import GridSearch, RandomSearch
    if args.search_algo == "grid":
        return GridSearch(max_candidates=args.max_candidates)
    return RandomSearch(seed=args.seed, n_samples=args.n_samples,
                        max_candidates=args.max_candidates)


def _build_objective_port(args) -> Any:
    from simulator.usecase.optimize_strategies import (
        NetProfitObjective, PfObjective, RecoveryObjective, SharpeObjective,
    )
    return {"pf": PfObjective, "net": NetProfitObjective,
            "sharpe": SharpeObjective, "recovery": RecoveryObjective}[args.objective]()
```

#### 2.4.5 出力整形（SP1 L-1 方針継承・新規 presenter なし）

```python
def to_json_dict(result: Any) -> dict:
    """OptimizeResult を JSON シリアライズ可能 dict へ（asdict パターン・SP1 踏襲）。"""
    from dataclasses import asdict
    return {
        "best_params": dict(result.best_params),
        "best_is_stats": asdict(result.best_is_stats),
        "best_is_score": result.best_is_score,
        "oos_stats": asdict(result.oos_stats),
        "degradation": [asdict(m) for m in result.degradation.metrics],
        "trials": [
            {"params": dict(t.params), "is_score": t.is_score,
             "is_finite": t.is_finite, "failed": t.failed,
             "failure_reason": t.failure_reason, "is_best": t.is_best}
            for t in result.trials  # is_stats は best_is_stats に集約済・trials では除外（肥大回避）
        ],
        "excluded_count": result.excluded_count,
        "total_candidates": result.total_candidates,
        "finite_candidates": result.finite_candidates,
    }


def to_markdown(result: Any, *, split=None, is_trading_start=None, objective=None) -> str:
    """best params 表＋IS best｜OOS｜劣化の並列表＋探索ログ表（人間可読・SP1 形式踏襲）。"""
```

- `trials[].is_stats`（保持された BacktestStats）は JSON では除外する（best のみ `best_is_stats` に集約・全候補 stats の出力肥大を回避）。`is_score`/`is_finite`/`failed`/`failure_reason`/`is_best` は全候補出力（探索ログ・FR-O8／除外内訳・C-1/M-1）。
- 整形は tools 層内で行い committed presenter（adapter）は改変も流用もしない（C2・新規 presenter 追加なし・SP1 継承）。

#### 2.4.6 main フロー

```python
def main(argv=None, *, repo_root=None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]
    out_dir = assert_safe_output_dir(args.out_dir, repo_root)  # SP1 再利用（先頭で検証）

    base_kwargs = _build_base_kwargs(args)         # build_interactor 固定引数
    search_space = _parse_search_space(args.search_param)
    factory, full_bars, split, is_start = make_run_segment_factory(
        base_kwargs, split_str=args.split, is_trading_start_str=args.is_trading_start)

    result = optimize(
        request=OptimizeRequest(search_space=search_space, split=split,
                                is_trading_start=is_start),
        full_bars=full_bars,
        make_run_segment=factory,
        search_port=_build_search_port(args),
        objective_port=_build_objective_port(args),
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "optimize.json").write_text(
        json.dumps(to_json_dict(result), indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(
        to_markdown(result, split=args.split, is_trading_start=args.is_trading_start,
                    objective=args.objective), encoding="utf-8")
    return 0
```

- `assert_safe_output_dir` を main 先頭で実行（SP1 と同様・無駄な build 前に出力先を拒否）。`OptimizeError`（上限超過・best 0 件）は main では捕捉せず送出（exit 非 0・無音禁止の明示中断）。

---

## 3. シーケンス／データフロー

### 3.1 シーケンス図（探索空間 → 候補列挙 → IS 探索 → best → OOS → 劣化 → 出力）

```
tools(optimize_cli)        main.build_interactor      usecase.optimize        search/objective Port   controller._interactor
      │                            │                        │                          │                       │
 (a) parse args（max_candidates 必須・M-3）                  │                          │                       │
 (b) base build_interactor(**base_kwargs)──►│              │                          │                       │
      │  ◄──(base_controller, base_request)  │              │                          │                       │
 (c) full_bars=base_request.bars / normalize split,is_start │                          │                       │
 (d) factory = make_run_segment_factory(base_kwargs)        │                          │                       │
 (e) optimize(request, full_bars, factory, search, objective)──────────────►│         │                       │
      │                            │      (1) is_bars=slice_is_bars(full,split)（SP1）│                       │
      │                            │      (1) 空区間検証 1 回（OptimizeError on NG）  │                       │
      │                            │      (2) candidates=search.candidates(space)────►│ 上限判定（M-2）       │
      │                            │                        │  ◄─決定論順 ParamSet 列─│ 超過→OptimizeError    │
      │                            │      (3) for params:  # N_cand 回                │                       │
      │                            │          rs=factory(params)                     │                       │
      │ (3) factory(params): build_interactor(**base,**params)──►│（CSV 再ロード・High-1）                   │
      │                            │  ◄──(controller,request)│                          │                       │
      │                            │          rs(is_bars, is_start)──────────────────────────────────────────►│ execute(IS)
      │                            │                        │  ◄──────────IS BacktestStats（or 例外 M-1 捕捉）──│
      │                            │          score=objective.score(is_stats)────────►│                       │
      │                            │          is_finite=math.isfinite(score)（C-1）   │                       │
      │                            │          TrialRecord 追記（is_stats 保持・High-2）│                       │
      │                            │      (4) finite==[] → OptimizeError（best 0 件）  │                       │
      │                            │      (4) best=argmax(finite, tie=先勝ち)         │                       │
      │                            │          best_is_stats=best.is_stats（再 run なし・High-2）              │
      │                            │      (5) rs_best=factory(best.params)（OOS 用別 build＝N_cand+1 番目）   │
      │                            │          rs_best(full, split)───────────────────────────────────────────►│ execute(OOS)
      │                            │                        │  ◄──────────OOS BacktestStats───────────────────│
      │                            │      (6) build_degradation_report(best_is, oos)（SP1）                    │
      │  ◄──────────────OptimizeResult(best_params, best_is_stats, oos_stats, degradation, trials, excluded)──│
 (f) assert_safe_output_dir（SP1・先頭で既実行）            │                          │                       │
 (g) to_json_dict / to_markdown                             │                          │                       │
 (h) write OUT/optimize.json, OUT/report.md                 │                          │                       │
```

### 3.2 データフロー（in-memory 完結・候補ごと build 再構築）

```
探索空間(search_space・読取)
   │  ParameterSearchPort.candidates（grid 辞書順 / random idx 昇順・決定論）＋上限判定（M-2）
   ▼
候補 params 列(ParamSet×N_cand) ──for each──► factory(params)=build_interactor(**base,**params)[CSV 再ロード・High-1]
                                                  │
   full_bars ──slice_is_bars(full,split)[head 切り・1 回]──► is_bars ─► run_segment(is_bars, is_start) ─► is_stats
                                                  │                                                          │
                                                  │                                          ObjectivePort.score ─► is_score
                                                  │                                          math.isfinite（C-1）─► is_finite
                                                  ▼                                                          ▼
                                          TrialRecord×N_cand{params,is_score,is_finite,failed,is_stats 保持} ◄┘
                                                  │ finite=[is_finite & not failed]; if []→OptimizeError（C-1/M-1）
                                                  │ argmax(finite, tie=先勝ち)
                                                  ▼
                                          best.params ─► factory(best) ─► run_segment(full, split) ─► oos_stats
                                          best_is_stats=best.is_stats（保持値・再 run なし・High-2）       │
                          build_degradation_report(best_is_stats, oos_stats)[SP1 再利用] ◄────────────────┘
                                                  ▼
                                          OptimizeResult → assert_safe_output_dir[SP1] → 新規 OUT(JSON/MD)
```

- run 回数＝IS（N_cand 回）＋OOS（best 1 回）＝**N_cand+1**（NFR-OP1）。best の IS run は再実行されず保持値を用いる（High-2）。
- CSV ロード回数＝base build 1 回（bars 取得・execute なし）＋候補ごと N_cand 回＋OOS 用 best 再 build 1 回（High-1・NFR-OP4）。中間バー列はプロセスメモリのみ（永続化なし）。

---

## 4. 物理データモデル（出力スキーマ）

本サブフェーズに RDB テーブルはない（オフライン分析ツール・プロセス内）。物理データモデルは「出力 JSON/Markdown スキーマ」として確定する。

### 4.1 `OUT/optimize.json` スキーマ

```json
{
  "best_params": { "<param 名>": "<scalar>" },
  "best_is_stats":  { "<BacktestStats の全フィールド>": "<number>" },
  "best_is_score": "<number>",
  "oos_stats": { "<BacktestStats の全フィールド>": "<number>" },
  "degradation": [
    { "name": "profit",        "is_value": "<f>", "oos_value": "<f>", "ratio": "<f|null>", "delta": "<f>" },
    { "name": "profit_factor", "is_value": "<f>", "oos_value": "<f>", "ratio": "<f|null>", "delta": "<f>" },
    { "name": "recovery_factor", "...": "..." },
    { "name": "expected_payoff", "...": "..." },
    { "name": "sharpe_ratio",    "...": "..." },
    { "name": "trades",          "...": "..." }
  ],
  "trials": [
    { "params": {"<name>": "<scalar>"}, "is_score": "<number|null>",
      "is_finite": "<bool>", "failed": "<bool>",
      "failure_reason": "<str|null>", "is_best": "<bool>" }
  ],
  "excluded_count": { "nonfinite": "<int>", "failed": "<int>" },
  "total_candidates": "<int>",
  "finite_candidates": "<int>"
}
```

- `best_is_stats`/`oos_stats` は `dataclasses.asdict(BacktestStats)`（`models.py:91-142` の全フィールド・型 float/int）。
- `degradation` は SP1 `MetricDegradation`（`run_is_oos.py:58-66`）の asdict 列。`ratio` は `null`（IS_value==0 時）または number。`delta` は常に number。順序は `metric_names`（既定 6 指標）に一致（決定論）。
- `trials` は全候補（成功・非有限・失敗を含む）。`is_stats` は出力肥大回避のため除外し best のみ `best_is_stats` に集約（§2.4.5）。`is_best` は best 1 件のみ true。
- `excluded_count.nonfinite`＝非有限スコア除外件数（C-1）、`.failed`＝例外失敗除外件数（M-1）。`total_candidates = finite_candidates + nonfinite + failed`。

### 4.2 `OUT/report.md` スキーマ（人間可読）

```markdown
# IS/OOS Optimization Report

- split: 2026-04-15
- is_trading_start: 2026-04-01
- objective: profit_factor
- candidates: total=<N> finite=<M> excluded(nonfinite=<a> failed=<b>)

## Best Parameters
| param | value |
|---|---|
| lot_size | 0.1 |
| stop_loss_points | 200 |

## IS(best) vs OOS Degradation
| metric | IS(best) | OOS | ratio (OOS/IS) | delta (OOS-IS) |
|---|---|---|---|---|
| profit | 11370.0 | -4020.0 | -0.354 | -15390.0 |
| trades | 5224 | 2438 | 0.467 | -2786 |

## Trial Log
| # | params | is_score | finite | failed | best |
|---|---|---|---|---|---|
| 0 | {lot_size:0.1,...} | 1.42 | yes | no | * |
```

- ratio 未定義（null）時はセルに `N/A`、delta は表示（SP1 継承）。
- 除外件数（nonfinite/failed）はヘッダの candidates 行で明示（無音切り捨て禁止・FR-O9）。

### 4.3 出力先（運用判断・SP1 統一）

許可 OUT は CLI `--out-dir` で渡された新規ディレクトリ配下のみ。SP1 `assert_safe_output_dir`（`run_is_oos_cli.py:67`）が `marketdata/`・`simulator/tests/fixtures/`・`simulator/tests/confirmation/` プレフィクス配下と repo_root 外を拒否（`OutputGuardError`）。具体命名規約は運用判断（例 `outputs/isoos_opt/<timestamp>/`・SP1 TBD-3 と統一）。

---

## 5. クリーンアーキテクチャ準拠の実装方針

### 5.1 レイヤ分割と依存方向

```
tools(optimize_cli)  ── import ──►  main.build_interactor          （Composition Root 利用側）
        │                          tools.run_is_oos_cli（SP1 make_run_segment/normalize_time 再利用）
        │  ── import ──►  usecase.optimize  ── import ──►  usecase.optimize_ports（Protocol）
        │                                      usecase.run_is_oos（slice_is_bars/build_degradation_report・SP1 部品）
        │                                      usecase.models（BacktestStats・Any 経由）
        │                                      domain.exceptions（ConfigError/BacktestError/MarginCallError）
        │  ── import ──►  usecase.optimize_strategies  ── import ──►  usecase.optimize_ports / run_is_oos.extract_metrics
        ▼
   controller._interactor.execute  （B-1: run/run_backtest は使わない）
```

| 規律 | 遵守手段 | 検証 |
|---|---|---|
| usecase→domain/同階層のみ（内向き） | `optimize.py`/`optimize_ports.py`/`optimize_strategies.py` は `pandas`・`simulator.main`・`simulator.adapter`・`simulator.tools` を import しない。エンジン実行手段は `make_run_segment: Callable` で注入（DIP） | `import ast` で禁止 import 不在を assert（§6.2.5・`test_optimize_dependency.py`） |
| tools→main/usecase | tools のみ `build_interactor`／SP1 `make_run_segment`・`normalize_time`・`assert_safe_output_dir` を import | — |
| SP1 無改変（C2） | SP1 `run_is_oos.py`／`run_is_oos_cli.py` は読み取り import のみ・属性改変なし。`run_is_oos` 関数は呼ばず純関数部品のみ利用 | `git diff` で SP1 ファイル差分 0（CI） |
| committed 無改変（C2/NFR-OS2） | 新規 4 ファイル＋テストのみ追加。`domain/exceptions.py`／`ports.py` 非編集（`OptimizeError` は `optimize.py` に定義） | `git diff` committed 差分 0（CI・R-O6） |

### 5.2 committed/SP1 無改変の保証

- **`OptimizeError` の配置**：committed `domain/exceptions.py`（§1.2 実証）を編集すると C2 違反のため、`OptimizeError` は usecase 新規ファイル `optimize.py` に定義（§2.3.2）。
- **`Port` 抽象**：`optimize_ports.py` を新規作成し committed `ports.py`（あれば）を非編集（基本設計 確定前提「`ports.py` 非編集」）。
- **SP1 部品再利用**：`slice_is_bars`／`build_degradation_report`／`extract_metrics`／`DegradationReport`／`RunSegment` を import するのみ。`run_is_oos` 関数は IS/OOS 対称 2 回呼び契約（`run_is_oos.py:134-135`）で非対称な最適化ループに不適合のため呼ばない（課題-O2）。SP1 純関数は副作用なし（`run_is_oos.py` 実装で実証）であり再利用が SP1 挙動を変えない。
- **`controller._interactor` 利用**：SP1 `make_run_segment` 経由（`run_is_oos_cli.py:48`）で読み取り利用のみ・属性改変なし（SP1 で実証済の利用形態を継承）。`request.bars`／`request.trading_start` への代入は committed `RunBacktestRequest` 公開フィールドへの書込で `build_interactor` 自身が設定する利用形態（SP1 §5.2 で実証）。

---

## 6. テスト設計

### 6.1 テスト全体方針とカバレッジ目標

| 区分 | 対象 | 自動化 | カバレッジ目標 |
|---|---|---|---|
| 単体 | GridSearch/RandomSearch・ObjectivePort・argmax/tie・max_candidates 超過・失敗除外/best0件・依存方向 | pytest（CI 自動） | 新規 `optimize.py`/`optimize_ports.py`/`optimize_strategies.py` の行・分岐 100%（純ロジック・到達可能） |
| 結合 | 単一候補空間で SP1 結合（先例 2026-04）と一致＋読み取り専用 fixture＋mtime 不変 | pytest（CI 自動・読み取り専用 fixture） | optimize 主経路（候補列挙→IS→argmax→OOS→劣化→出力）の end-to-end |
| 回帰 | 決定論再現（同一入力→同一 best・byte 同一）／退行禁止（OOS 全候補 run・best IS 再 run・MarginCall 握り潰し・非有限 argmax 混入） | pytest（CI 自動） | 決定論破れ・退行を禁止 |

- 回帰テスト方針（user memory「bugfix-pair-with-regression-test」）：基本設計 §8.2 が列挙する 4 退行（grid 順非決定・random seed 未固定・tie 非決定・非有限 argmax 混入／OOS 全候補 run／best IS 再 run／MarginCall 握り潰し）を禁止する回帰テストを各 1 本添える。

### 6.2 単体テスト

#### 6.2.1 `test_grid_search.py`（辞書順候補列挙・上限拒否）

合成小データ（`marketdata` 非依存）で `search_space` のみを入力（エンジン不要）。

| ケース | 入力 | 期待 |
|---|---|---|
| 辞書順全列挙 | `{"b":[1,2], "a":[10,20]}`, max=99 | キー昇順 `a,b`／右端 `b` が速く回る：`[{a:10,b:1},{a:10,b:2},{a:20,b:1},{a:20,b:2}]` |
| 挿入順=keys 昇順 | 上記 | 各 dict のキー順が `a,b`（決定論） |
| theoretical_count | `{"a":[1,2,3],"b":[1,2]}` | 6 |
| 上限超過拒否（M-2） | N_space=6, max=5 | `OptimizeError`（context.theoretical=6, max=5）。1 件も yield しない |
| 上限ちょうど | N_space=6, max=6 | 全 6 件 yield（拒否しない） |

#### 6.2.2 `test_random_search.py`（seed 固定再現・整数抽出・全件）

| ケース | 入力 | 期待 |
|---|---|---|
| seed 固定で同一候補列 | seed=42, n=3, N_space=10, max=99（2 回呼ぶ） | 2 回の candidates が同一 ParamSet 列（byte 同一・High-3） |
| インデックス昇順列挙 | seed 固定 | 選択 idx を `sorted` した昇順で復号（FO-02 後条件） |
| n_samples>N_space 全件 | n=100, N_space=6, max=99 | 6 件全件（k=min(100,6)=6） |
| 上限超過拒否（M-2） | k=min(n,N)=6, max=5 | `OptimizeError`（algo=random） |
| grid と同一基準順序 | 同 search_space で GridSearch 全列挙の idx と RandomSearch の `_decode_index` が一致 | random で選んだ idx の復号 = grid 列挙の同 idx 要素（基準順序共有） |

#### 6.2.3 `test_objective_ports.py`（score・非有限・tie）

`BacktestStats` を最小フィールドで構築するヘルパ（目的関数フィールドのみ意味を持つ）。

| ケース | 対象 | 期待 |
|---|---|---|
| PfObjective | stats.profit_factor=1.42 | score=1.42（大きいほど良い） |
| NetProfitObjective | stats.profit=11370.0 | score=11370.0 |
| SharpeObjective | stats.sharpe_ratio=0.8 | score=0.8 |
| RecoveryObjective | stats.recovery_factor=2.1 | score=2.1 |
| 非有限 NaN | profit_factor=`float("nan")` | `math.isfinite(score)` が False（除外対象・C-1） |
| 非有限 +inf | profit_factor=`float("inf")`（gross_loss=0 相当） | `math.isfinite(score)` が False（除外対象・C-1） |
| argmax tie 先勝ち | finite=[(p1,1.0),(p2,1.0),(p3,0.5)] 列挙順 | best=p1（厳密 `>` のみ更新＝先勝ち・NFR-OD1） |

#### 6.2.4 `test_optimize_core.py`（失敗除外・best0件・run 回数・保持値）

合成 run_segment スタブ（params→固定 stats を返す callable）＋呼出回数カウンタで、エンジン非依存に UC ロジックを検証。

| ケース | スタブ挙動 | 期待 |
|---|---|---|
| 失敗候補除外（M-1） | 候補 #1 で `MarginCallError` を raise | trials に failed=True/failure_reason 記録、continue、best は残り候補から確定 |
| ConfigError/BacktestError 捕捉（M-1） | 候補 #2 で `ConfigError` | 同上（除外・継続） |
| best 0 件中断（C-1/M-1） | 全候補が非有限 or 失敗 | `OptimizeError`（context.finite_candidates=0・total/excluded 内訳） |
| run 回数 N_cand+1（High-2/R-O5） | N_cand 候補・全成功 | run_segment 呼出＝IS N_cand 回＋OOS 1 回。**best の IS 再 run なし**（IS 呼出が N_cand を超えない） |
| best_is_stats 保持値（High-2） | best 候補の is_stats を識別子付きで返す | `result.best_is_stats` が探索中の保持インスタンスと同一（再 run で別インスタンスにならない） |
| excluded_count 内訳（C-1/M-1） | nonfinite 2 件・failed 1 件 | `excluded_count={"nonfinite":2,"failed":1}`・total=finite+3 |
| 空区間中断 | full_bars が IS 0 件 | `OptimizeError`（phase=pre_validation） |

#### 6.2.5 `test_optimize_dependency.py`（依存方向・ast 禁止 import 不在）

```python
# 擬似（決定論的契約）
import ast
FORBIDDEN = {"pandas", "simulator.main", "simulator.adapter", "simulator.tools"}
for path in ["simulator/usecase/optimize.py",
             "simulator/usecase/optimize_ports.py",
             "simulator/usecase/optimize_strategies.py"]:
    tree = ast.parse(Path(path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] not in {"pandas"} and a.name not in FORBIDDEN
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod.split(".")[0] != "pandas"
            assert not any(mod == f or mod.startswith(f + ".") for f in FORBIDDEN)
```

- usecase→domain（`simulator.domain.exceptions`）・同階層（`usecase.run_is_oos`/`usecase.models`/`usecase.optimize_ports`）は許容。`simulator.main`/`adapter`/`tools`/`pandas` の不在を assert（クリーンアーキ依存方向・NFR-OS2）。

### 6.3 結合テスト `test_optimize_sp1_degenerate.py`（SP1 縮退・先例 2026-04 再現）

`simulator/tests/confirmation/2026-04_stop-probe_oos/bars_m1.csv`（full・読み取り専用）を `data_path` に、探索空間を**単一候補（= SP1 の固定 params）**へ縮退させ、best=その候補となることで IS/OOS stats・劣化が SP1 結合（`test_is_oos_stop_probe.py`）と一致することを実証する。

```python
# 擬似（決定論的契約・SP1 詳細 §6.3 と同一 base_kwargs）
base_kwargs = dict(
    data_path=str(bars_m1_csv), symbol="JP225", period="M1", ea_name="StopEntryProbe_EA",
    initial_deposit=10000.0, contract_size=10.0, volume_min=0.01, volume_max=100.0,
    volume_step=0.01, stops_level=0, digits=1, point_size=0.1, leverage=10.0,
    ma_period=60, ma_method="ema", lot_size=0.1, stop_loss_points=200,
    take_profit_points=500, entry_offset_points=100.0, entry_type="stop",
    config_overrides={  # reconcile.py:112-124 と同一（決定論 config）
        "tick_model": "ohlc_expand", "entry_price_basis": "current_open",
        "floating_pnl_basis": "bid_ask", "stop_out_action": "close_and_halt",
        "session_calendar": "jp225", "profit_round_digits": 0, "stop_out_at_open": True,
        "pending_lifecycle": True, "pending_oco": True, "pending_persistent": True,
        "hedged_margin": True,
    },
    stop_out_level=100.0,
)
factory, full_bars, split, is_start = make_run_segment_factory(
    base_kwargs, split_str="2026-04-15", is_trading_start_str="2026-04-01")
# 単一候補空間（base 既定と同じ stop_loss_points=200 のみ＝縮退）
result = optimize(
    request=OptimizeRequest(search_space={"stop_loss_points": [200]},
                            split=split, is_trading_start=is_start),
    full_bars=full_bars, make_run_segment=factory,
    search_port=GridSearch(max_candidates=10), objective_port=NetProfitObjective(),
)
# best=唯一候補。IS は SP1 IS（reconcile_is.py:32-34）と一致
assert result.best_params == {"stop_loss_points": 200}
assert result.best_is_stats.trades == 5224
assert result.best_is_stats.profit == 11370.0
# OOS は SP1 OOS（reconcile.py:32-34）と一致
assert result.oos_stats.trades == 2438
assert result.oos_stats.profit == -4020.0
assert result.total_candidates == 1 and result.finite_candidates == 1
assert result.excluded_count == {"nonfinite": 0, "failed": 0}
```

- **SP1 結合との一致根拠**：単一候補空間では `optimize` の IS run＝SP1 の IS run（同一 `slice_is_bars`＋同一 run_segment＝execute 直叩き B-1）、OOS run＝SP1 の OOS run（best=唯一候補・同一 full+split）。よって IS/OOS stats は SP1 詳細 §6.3 の先例値（IS net+11370/5224・OOS net-4020/2438）と byte 一致する。SP1 詳細 §1.2「IS が full の head-prefix」実証を継承。
- **読み取り専用 fixture／mtime 不変（NFR-OS1）**：実行前後に既存データディレクトリ（`marketdata/`・`fixtures/`・`confirmation/`）配下の全ファイル mtime を採取し不変を assert（SP1 §6.4 `snapshot_mtimes` パターン継承）。OUT は `tmp_path` 配下で `assert_safe_output_dir` を通す（二重に非波及担保）。

### 6.4 回帰テスト（決定論再現・byte 同一）

```python
# 擬似（決定論的契約）
r1 = optimize(...同一入力...)
r2 = optimize(...同一入力...)
assert to_json_dict(r1) == to_json_dict(r2)            # best・trials・除外内訳が同一
assert json.dumps(to_json_dict(r1)) == json.dumps(to_json_dict(r2))  # byte 同一（NFR-OD1）
```

- grid 列挙順・random seed 固定・tie 先勝ち・非有限除外が決定論であることを byte 同一で固定（基本設計 NFR-OD1）。退行禁止（§6.1）の 4 観点を本回帰でカバー。

### 6.5 自動化範囲

- 単体（GridSearch/RandomSearch/ObjectivePort/UC core/依存方向）・結合（SP1 縮退・mtime 不変）・回帰（決定論 byte 同一）：すべて pytest で CI 自動化。
- E2E（tools CLI）：`optimize_cli.main(argv=[...])` を関数呼出で起動し OUT 生成を assert（subprocess 不要・決定論・SP1 §6.5 方針継承）。

---

## 7. 後段③ウォークフォワード（WF）への拡張点

基本設計「後段サブフェーズ③への拡張余地」を実装契約で具体化する。

| 拡張先 | 再利用する本設計の契約 | 拡張方法 |
|---|---|---|
| ③WF `usecase/walk_forward.py` | `optimize(request, full_bars, make_run_segment, search_port, objective_port)` 公開関数 | anchored/rolling 窓 i ごとに `split_i`・`is_trading_start_i` を `OptimizeRequest` へ与え `optimize` を**反復呼出**。窓ごとの `OptimizeResult` を収集・連結 |
| ③WF | `OptimizeRequest`/`OptimizeResult` の窓非依存性 | `split`/`is_trading_start` を Request 引数化済（窓ごとに差し替え可能）。WF は窓ループから IF 不変で呼べる |
| ③WF | `MakeRunSegment = Callable[[ParamSet], RunSegment]` 契約 | 窓ごとに full_bars（窓のバー範囲）と make_run_segment を差し替える（UC 契約不変） |

- **IF 安定性の保証**：`optimize` の引数（`request`/`full_bars`/`make_run_segment`/`search_port`/`objective_port`）と戻り値（`OptimizeResult`）を窓非依存に保つことで、WF が窓ループから `optimize` をそのまま呼べる。計算コストは窓数 W × (N_cand+1) run（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §4 の非同期ジョブ化・並列・キャッシュはこの段で本格適用）。
- いずれも committed エンジン・SP1・本 UC は無改変のまま、`walk_forward` が `optimize` の「上」に重なる構造（クリーンアーキ・レイヤリング図に整合）。

---

## 8. 制約遵守チェックリスト（DoD 対応）

| 制約 | 遵守手段 | 検証 |
|---|---|---|
| C1 既存データ非波及 | SP1 `assert_safe_output_dir` 再利用＋OUT を tmp/新規限定 | `test_optimize_sp1_degenerate.py` mtime 不変 assert（§6.3） |
| C2 committed/SP1 無改変 | 新規 4 ファイル＋テストのみ。`OptimizeError` は `optimize.py` 定義（`domain/exceptions.py` 非編集）・`ports.py` 非編集・SP1 読み取り import のみ・`run_is_oos` 関数非呼出 | `git diff` committed/SP1 差分 0（CI・R-O6） |
| C3 技術スタック追加禁止 | 純 Python＋`dataclasses`/`typing`/`itertools`/`random`/`math`/`argparse`/`pathlib`＋既存 `pandas`（tools 層のみ） | `test_optimize_dependency.py`＋import 検査 |
| 基本設計逸脱なし | §1.1 のとおり確定事項（C-1/High-1/High-2/High-3/M-1/M-2/M-3）の具体化のみ | 本書 §10 根拠表 |
| 4 領域文書化 | モジュール詳細（§2）・物理データモデル（§4）・API/IF（§2.1-2.3/§6.1）・テスト設計（§6） | 本書 §2〜§6 |
| 実装着手可能水準 | クラス名・メソッドシグネチャ・参照実装・テスト契約を明記 | §2〜§6 |

---

## 9. リスク・残存課題

| ID | 課題 | 対応 |
|---|---|---|
| 残-O1 | TBD-O2 CSV 再ロード削減（N_cand+1 回） | committed IF 変更を要し本サブフェーズ範囲外。正しさ優先で full ビルド許容（High-1）。後段で build への bars 注入／registry キャッシュを検討（プロファイルで CSV ロードが支配的か測定） |
| 残-O2 | TBD-O4 出力パス命名規約 | `--out-dir` 引数化＋`assert_safe_output_dir`。具体命名は運用判断（SP1 と統一） |
| 残-O3 | TBD-O5 多目的最適化 | 単一スカラ argmax に限定（YAGNI）。後段で `ObjectivePort` 複合化を検討 |
| 残-O4 | TBD-O6 「小さいほど良い」指標の符号反転 | 既定外（YAGNI）。将来 `ObjectivePort` 実装側で符号反転して大きいほど良いに正規化（OCP・既存無改変） |
| 残-O5 | no-op 候補（戦略未参照パラメータ） | 探索空間定義時に当該 ea_name 参照パラメータのみ選択（基本設計 §5.5）。探索ログの is_score 候補間不変で no-op 検出可能 |

---

## 10. 設計判断の根拠・代替案比較・定量評価

| # | 判断項目 | 採用 | 代替案 | 根拠 | パフォーマンス影響（定量） |
|---|---|---|---|---|---|
| 1 | Port 抽象の表現 | `typing.Protocol`（構造的部分型） | `abc.ABC` 継承 | C3 追加なし。実装が `optimize_ports` を import せずとも準拠可（疎結合）。テスト用 callable 実装も受入 | 0（型レベルのみ） |
| 2 | 上限判定の IF | `theoretical_count`＋`candidates` 内で列挙前判定 | 列挙後に件数判定 | 巨大 grid のメモリ一括確保回避。超過時 1 件も yield せず拒否（M-2） | 超過時 O(キー数)で即拒否（O(N_space) 列挙を回避） |
| 3 | `OptimizeError` の所在 | `optimize.py` に一次定義・戦略から import | `domain/exceptions.py` に追加 / 各ファイルに重複定義 | C2（committed `domain/exceptions.py` 無改変）。型の単一所在。循環は関数内 import 不要（戦略→optimize は単方向） | 0 |
| 4 | random 抽出方式 | `random.Random(seed).sample(range(N_space), k)` 整数インデックス | float 値直接サンプル / 値タプル sample | High-3 確定。float 等価比較の重複判定不安定を回避。seed 固定で決定論 | O(k) 抽出。idx→ParamSet 復号 O(k×キー数) |
| 5 | `OptimizeError` の基底 | `Exception` 直系（`BacktestError` 非継承） | `BacktestError` 継承 | 候補捕捉の `except BacktestError`（M-1）が `OptimizeError`（制御中断）を誤って握り潰さない | 0 |
| 6 | 候補失敗の捕捉型 | `(ConfigError, BacktestError, MarginCallError)` 並記 | `except BacktestError` のみ | M-1 確定（execute 直叩きで翻訳掛からず・`exceptions.py:84,92`／`main:434-439`）。MarginCall は孫だが捕捉対象を IF 宣言 | 0（捕捉のみ） |
| 7 | 空区間検証の例外型 | `OptimizeError`（phase=pre_validation） | SP1 `IsOosValidationError` 流用 | `run_is_oos` 関数を呼ばない（部品のみ再利用）ため `IsOosValidationError` は発生しない。最適化中断は `OptimizeError` で統一 | O(N)（OOS 件数カウント 1 回・探索前） |
| 8 | best_is_stats 保持（High-2） | TrialRecord に IS 結果を保持し best で採用（再 run なし） | best の IS を再 run | total run = N_cand+1 厳守・同一 params 二重 run 回避 | IS run を 1 回削減（N_cand+1 vs N_cand+2） |
| 9 | trials の is_stats を JSON 除外 | best のみ `best_is_stats` 出力・trials は score/flag のみ | 全候補 stats を JSON 出力 | 出力肥大回避（N_cand 個の全 BacktestStats 抑制）。探索ログは score/採否で十分（FR-O8） | 出力サイズ O(N_cand×stats) → O(N_cand) |

### 10.1 パフォーマンス全体評価（NFR-OP1/OP2）

- **エンジン実行回数**：IS（N_cand 回）＋OOS（best 1 回）＝**N_cand+1**（NFR-OP1）。OOS は best のみ（全候補 OOS run の 2×N_cand を回避・R-O5）。best の IS は保持値採用で再 run なし（High-2・判断 8）。
- **CSV ロード回数**：base build 1 回（bars 取得・execute なし）＋候補ごと N_cand 回＋OOS 用 best 再 build 1 回＝**N_cand+2 回**（High-1・NFR-OP4・committed IF 起因で不可避・TBD-O2 で降格）。
- **上限判定**：`theoretical_count` で O(キー数) 即算出、超過は 1 件も列挙せず拒否（判断 2）。grid の N_cand = Π(各値リスト長)。
- **劣化算出**：`build_degradation_report` の 6 フィールド O(1) 算術（バー数 N 非依存・SP1 継承）。
- **argmax**：有限母集合 O(finite) の `>` 線形走査（tie 先勝ち）。

---

## 11. 参考資料（実証済・実コード）

- `simulator/usecase/run_is_oos.py`（SP1・`slice_is_bars` L26 ／ `extract_metrics` L91 ／ `build_degradation_report` L96 ／ `DegradationReport`/`MetricDegradation` L58-79 ／ `RunSegment` L23 ／ `run_is_oos` IS/OOS 対称 L134-135）
- `simulator/tools/run_is_oos_cli.py`（SP1・`make_run_segment` L38-51 ／ `normalize_time` L54-64 ／ `assert_safe_output_dir`・`_FORBIDDEN_PREFIXES` L31-35,67-83 ／ `controller._interactor.execute` L48）
- `simulator/main/__init__.py`（`build_interactor` L256-285＝探索対象 params の実体 ／ CSV 再ロード L335,344・申し送り L339-342 ／ `run_backtest` 翻訳が build 段階のみ L434-439）
- `simulator/domain/exceptions.py`（`BacktestError` L24 ／ `ConfigError` L52 ／ `ExecutionError` L84 ／ `MarginCallError` L92＝ExecutionError の孫）
- `simulator/usecase/models.py`（`BacktestStats` L91-142＝目的関数/劣化対象フィールド・PF/Sharpe/Recovery が float＝NaN/inf を取り得る L100-103）
- `.doc/ISOOS_OPTIMIZATION_BASIC_DESIGN.md` v0.2.0（上位設計・唯一の正）
- `.doc/ISOOS_SIMPLE_SPLIT_DETAILED_DESIGN.md` v1.0.0（SP1 詳細・部品契約・IS=full head-prefix 実証 §1.2・先例 2026-04 §6.3）
