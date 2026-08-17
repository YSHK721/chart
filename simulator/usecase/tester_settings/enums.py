"""Settings タブの列挙 10 種と `.ini` ラベル写像（基本設計 §4.3・内部設計 §4.2.2）。

1. 層名/責務:
    usecase 層（内側 DTO の一部）。`.ini` / MQL の生値と 1:1 に対応する語彙だけを
    定義する。I/O・検証・変換は行わない（純粋な値の定義）。

2. 含む構造:
    Timeframe / TickModel / DateRangeKind / DatesPreset / ForwardMode /
    OptimizationMode / OptimizationCriterion / SubjectKind / InputForm の 9 列挙と、
    名前付き定数 ExecutionDelay（生 int を保持するためフィールド型にしない）。
    TIMEFRAME_INI_LABELS / INI_LABEL_TO_TIMEFRAME: `Period` のラベル写像。
    TICK_MODEL_ENGINE_IDS: `Model` → 現行エンジンの tick_model id（§6.2）。

3. 元 MQL 対応:
    ENUM_TIMEFRAMES（Timeframe の数値）ほか、`.ini` の Model / Dates / ForwardMode /
    Optimization / OptimizationCriterion の生値と値一致させる（`common/applied_price.py`
    の AppliedPrice と同じ流儀）。UI の表示順を値として用いてはならない（基本設計 §4.3.2）。

4. 依存:
    標準: enum
    外部: なし
    プロジェクト内: なし（domain と同格の純粋な値定義）

実証状態の凡例（基本設計 §4.3 の実測に対応）:
    実証   = corpus または fixture の実測で確認済み
    暫定   = 実測がなく、消去法・UI 表示からの推定（TBD 番号を併記）
    未実証 = 本リポジトリ内に根拠がなく MQL5 公式リファレンスとの照合が必要（TBD-10/11）
"""
from __future__ import annotations

from enum import IntEnum, StrEnum


class Timeframe(IntEnum):
    """`Period`（`.ini` は文字列ラベル・値は MQL ``ENUM_TIMEFRAMES`` と一致）。

    数値のうち本リポジトリ内で実証されているのは ``MN1``（49153・F-15）のみで、
    他は MQL5 公式リファレンス由来＝未実証（TBD-11）。ラベルの実証は
    ``H1`` / ``H8`` / ``D1`` の 3 件のみ（corpus 実測・TBD-10）。
    """

    M1 = 1
    M2 = 2
    M3 = 3
    M4 = 4
    M5 = 5
    M6 = 6
    M10 = 10
    M12 = 12
    M15 = 15
    M20 = 20
    M30 = 30
    H1 = 16385
    H2 = 16386
    H3 = 16387
    H4 = 16388
    H6 = 16390
    H8 = 16392
    H12 = 16396
    D1 = 16408
    W1 = 32769
    MN1 = 49153


#: `Period` の `.ini` ラベル（D-03）。行末の注記が実証状態。
TIMEFRAME_INI_LABELS: dict[Timeframe, str] = {
    Timeframe.M1: "M1",       # 暫定（TBD-10。画像 1 の UI 表示のみ）
    Timeframe.M2: "M2",       # 未実証（TBD-10）
    Timeframe.M3: "M3",       # 未実証
    Timeframe.M4: "M4",       # 未実証
    Timeframe.M5: "M5",       # 未実証
    Timeframe.M6: "M6",       # 未実証
    Timeframe.M10: "M10",     # 未実証
    Timeframe.M12: "M12",     # 未実証
    Timeframe.M15: "M15",     # 未実証
    Timeframe.M20: "M20",     # 未実証
    Timeframe.M30: "M30",     # 未実証
    Timeframe.H1: "H1",       # 実証（corpus 実測）
    Timeframe.H2: "H2",       # 未実証
    Timeframe.H3: "H3",       # 未実証
    Timeframe.H4: "H4",       # 未実証
    Timeframe.H6: "H6",       # 未実証
    Timeframe.H8: "H8",       # 実証（corpus 実測）
    Timeframe.H12: "H12",     # 未実証
    Timeframe.D1: "Daily",    # 実証（corpus 実測）
    Timeframe.W1: "Weekly",   # 未実証（TBD-10）
    Timeframe.MN1: "Monthly", # 未実証（TBD-10）
}

#: ラベル → 列挙（読取方向）。写像は上表の逆で導出する（手書きの second table を作らない）。
INI_LABEL_TO_TIMEFRAME: dict[str, Timeframe] = {
    label: timeframe for timeframe, label in TIMEFRAME_INI_LABELS.items()
}


class TickModel(IntEnum):
    """`Model`（Modelling）。値は `.ini` の生値と一致（UI 表示順ではない）。"""

    EVERY_TICK = 0          # 実証（F-3: コメント every tick）
    ONE_MINUTE_OHLC = 1     # 実証（F-4: m1 ohlc）
    OPEN_PRICES_ONLY = 2    # 実証（F-5: open prices）
    MATH_CALCULATIONS = 3   # 暫定（TBD-01。corpus 未出現・消去法）
    REAL_TICKS = 4          # 実証（F-6: real ticks）


#: `Model` → 現行エンジンの tick_model id（基本設計 §6.2）。
#: ``MATH_CALCULATIONS`` は現行レジストリに対応 id を持たないため**含めない**
#: （別経路＝内部設計 §8.2）。値が ``TICK_MODEL_IDS`` の部分集合であることは
#: 契約ガード（tests: 契約不変ガード）が実レジストリと突合して固定する。
TICK_MODEL_ENGINE_IDS: dict[TickModel, str] = {
    TickModel.EVERY_TICK: "every_tick",
    TickModel.ONE_MINUTE_OHLC: "ohlc_expand",
    TickModel.OPEN_PRICES_ONLY: "open_only",
    TickModel.REAL_TICKS: "real_ticks",
}


class DateRangeKind(StrEnum):
    """期間指定の形（`.ini` のキー構成。MQL 由来の数値を持たない）。"""

    PRESET = "preset"   # Dates=<int>
    CUSTOM = "custom"   # FromDate + ToDate


class DatesPreset(IntEnum):
    """`Dates`。corpus 実測値のみを定義する（1・3 以降は未知値として拒否）。"""

    ENTIRE_HISTORY = 0  # 実証（F-7: entire history）
    LAST_YEAR = 2       # 実証（F-7: last year）


class ForwardMode(IntEnum):
    """`ForwardMode`。値 1・2 は corpus 未出現のため定義しない（未知値は拒否）。"""

    DISABLED = 0        # 実証（F-9）
    PRESET_SPLIT = 3    # 実証（F-9/F-10）。分割位置は未確定（TBD-03）
    CUSTOM_DATE = 4     # 実証（F-10。ForwardDate を必ず伴う）


class ExecutionDelay:
    """`ExecutionMode` の**名前付き定数**（列挙にしない）。

    フィールドは生 ``int`` で保持する（基本設計 §4.3.5）。意味が実証済みの 2 値
    のみを命名し、corpus 実測の ``-1`` / ``21`` には名前を与えない（未実証の意味を
    確定事実として下流へ伝播させないため）。
    """

    ZERO_LATENCY_IDEAL: int = 0   # 暫定（TBD-08。画像 1 のラベル対応は未取得）
    DELAY_50MS: int = 50          # 実証（golden fixture の delays_ms=50 と一致）

    def __init__(self) -> None:  # pragma: no cover - 定数名前空間のため生成しない
        raise TypeError("ExecutionDelay は定数の名前空間であり生成できません")


class OptimizationMode(IntEnum):
    """`Optimization`。値 3 は corpus 未出現のため定義しない（TBD-04）。"""

    DISABLED = 0              # 実証（F-8）
    FULL_SLOW_COMPLETE = 1    # 実証（F-8: Full optimization）
    GENETIC = 2               # 実証（F-8: Genetic optimization）


class OptimizationCriterion(IntEnum):
    """`OptimizationCriterion`。数値と評価軸の対応が未確定のため名前を与えない（TBD-05）。"""

    CRITERION_0 = 0  # 実証（値の存在のみ・31 件中 25 件）
    CRITERION_1 = 1  # 実証（値の存在のみ・31 件中 6 件）


class SubjectKind(StrEnum):
    """テスト対象の種別（`Expert` / `Indicator` のどちらのキーを持つか）。"""

    EXPERT = "expert"
    INDICATOR = "indicator"


class InputForm(StrEnum):
    """`[TesterInputs]` の 1 行の形（基本設計 §4.3.8）。"""

    SCALAR = "scalar"   # 名前=値（|| なし・F-14）
    RANGE_5 = "range5"  # 名前=現在値||開始値||刻み||終了値||{Y|N}（F-13）
