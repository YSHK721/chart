"""Settings タブの列挙 10 種と `.ini` ラベル写像（基本設計 §4.3・内部設計 §4.2.2）。

1. 層名/責務:
    usecase 層（内側 DTO の一部）。`.ini` / MQL の生値と 1:1 に対応する語彙と、
    その**実証状態**を定義する。I/O・検証・値の変換は行わない（値の定義と、
    定義済みの値だけを引数に取る純関数に限る）。

2. 含む構造:
    Timeframe / TickModel / DateRangeKind / DatesPreset / ForwardMode /
    OptimizationMode / OptimizationCriterion / SubjectKind / InputForm の 9 列挙と、
    名前付き定数 ExecutionDelay（生 int を保持するためフィールド型にしない）と、
    その**実証状態の唯一の宣言**（PROVEN_EXECUTION_DELAYS /
    PROVISIONAL_EXECUTION_DELAYS / approximation_reason_for）。
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
#: A-1（ISSUE-397）で ``MATH_CALCULATIONS`` もレジストリ id を持つようになったため、
#: 本表は `TickModel` の**全値**を写す（別経路＝旧 §8.2 は撤去された）。値が
#: ``TICK_MODEL_IDS`` の部分集合であること・本表が全値を覆うことは、契約ガード
#: （tests: 契約不変ガード）が実レジストリと突合して固定する。
#: 文字列を adapter から import しないのは層規約（usecase は adapter を参照しない）。
TICK_MODEL_ENGINE_IDS: dict[TickModel, str] = {
    TickModel.EVERY_TICK: "every_tick",
    TickModel.ONE_MINUTE_OHLC: "ohlc_expand",
    TickModel.OPEN_PRICES_ONLY: "open_only",
    TickModel.MATH_CALCULATIONS: "math_calculations",
    TickModel.REAL_TICKS: "real_ticks",
}


class DateRangeKind(StrEnum):
    """期間指定の形（`.ini` のキー構成。MQL 由来の数値を持たない）。"""

    PRESET = "preset"   # Dates=<int>
    CUSTOM = "custom"   # FromDate + ToDate


class DatesPreset(IntEnum):
    """`Dates`。実測値のみを定義する（3 以降は未知値として拒否）。

    実測源: corpus（F-7）＋ MT5 保存 ini（`.doc/mt5_options/` 2026-09-06）。
    """

    ENTIRE_HISTORY = 0  # 実証（F-7: entire history／mt5_options: 全履歴.ini）
    LAST_MONTH = 1      # 実証（mt5_options: 先月.ini 2026-09-06）
    LAST_YEAR = 2       # 実証（F-7: last year／mt5_options: 昨年.ini）


class ForwardMode(IntEnum):
    """`ForwardMode`。全 5 値実測（corpus ＋ mt5_options 保存 ini・2026-09-06）。

    分割位置は保存 ini のファイル名（UI 選択肢との対応）で確定した——TBD-03 解消。
    """

    DISABLED = 0        # 実証（F-9）。MT5 表示「キャンセル」
    SPLIT_HALF = 1      # 実証（mt5_options: フォーワードテスト_2_1.ini＝UI「1/2」）
    SPLIT_THIRD = 2     # 実証（mt5_options: フォーワードテスト_3_1.ini＝UI「1/3」）
    SPLIT_QUARTER = 3   # 実証（F-9/F-10 ＋ mt5_options: フォーワードテスト_4_1.ini＝UI「1/4」）
    CUSTOM_DATE = 4     # 実証（F-10。ForwardDate を必ず伴う）


class ExecutionDelay:
    """`ExecutionMode` の**名前付き定数**（列挙にしない）。

    フィールドは生 ``int`` で保持する（基本設計 §4.3.5）。命名するのは「意味の主張
    （実証または暫定）を持つ値」だけであり、主張の実証状態は下の
    ``PROVEN_EXECUTION_DELAYS`` / ``PROVISIONAL_EXECUTION_DELAYS`` が宣言する。
    corpus 実測の ``-1`` / ``21`` には名前を与えない（意味を推定できない値に主張を
    作らない。「ランダム遅延」「カスタム遅延」「ping 由来」の生値は未実測のまま）。
    """

    ZERO_LATENCY_IDEAL: int = 0   # 暫定（TBD-08。UI「遅延ゼロ、理想的な実行」との対応は推定）
    DELAY_1MS: int = 1            # 暫定（TBD-20。UI「1ミリ秒」＝生値 ms の類推）
    DELAY_5MS: int = 5            # 暫定（TBD-20）
    DELAY_10MS: int = 10          # 暫定（TBD-20）
    DELAY_20MS: int = 20          # 暫定（TBD-20）
    DELAY_50MS: int = 50          # 実証（golden fixture の delays_ms=50 と一致）
    DELAY_100MS: int = 100        # 暫定（TBD-20。MT5 実画面 ss20260906195130 の選択値）
    DELAY_500MS: int = 500        # 暫定（TBD-20）
    DELAY_1000MS: int = 1000      # 暫定（TBD-20）

    def __init__(self) -> None:  # pragma: no cover - 定数名前空間のため生成しない
        raise TypeError("ExecutionDelay は定数の名前空間であり生成できません")


# --- `ExecutionDelay` の実証状態（唯一の宣言場所） ---------------------------------
# 「名前を与えた」ことと「意味が実証されている」ことは別の事実であり、上の宣言コメント
# （暫定 / 実証）がその唯一の根拠である。これを別モジュールや外側の層（`main` の
# `kwargs_mapper` 等）で判定すると、定数の宣言と実証状態の判定が離れ、片方だけが更新
# される。ここで宣言し、外側は読むだけにすることで判定箇所は 1 つになる。
#
# 以前の実装は `kwargs_mapper` で `vars(ExecutionDelay)` を走査し「名前がある＝実測済み」
# とみなしていたため、`ExecutionMode=0`（暫定・TBD-08）が「近似ではない」として呼出側へ
# 伝わっていた（実測）。
#
# 宣言漏れの扱い: 定数を増やして実証状態を書き忘れた場合、`PROVEN` にも `PROVISIONAL`
# にも入らないため「名前なし」と同じ扱い（＝近似）に倒れる。安全側であり、かつ網羅ゲート
# （`test_execution_delay_evidence.py`）が漏れ自体を検出して落とす。

#: 意味が**実証済み**の遅延。上の宣言コメント「実証（golden fixture の delays_ms=50 と
#: 一致）」が根拠であり、実証があるのはこの 1 値だけである。
PROVEN_EXECUTION_DELAYS: "frozenset[int]" = frozenset({ExecutionDelay.DELAY_50MS})

#: 名前は与えたが意味が**暫定**の遅延 → 未確定事項番号。上の宣言コメント
#: 「暫定（TBD-08。画像 1 のラベル対応は未取得）」が根拠。
#:
#: TBD-20（2026-09-06）: MT5「延滞」ドロップダウンの実測スクショ
#: （.doc/mt5_options/ss20260906204849.jpg）に「N ミリ秒」形の選択肢が並ぶ。
#: 数値ラベル＝生値ミリ秒との対応は、実証済みの 50（DELAY_50MS ⇔「50ミリ秒」）からの
#: 類推＝**暫定**（各値の保存 ini は未取得）。「ランダム遅延」「カスタム遅延」「ping 由来」
#: は生値を推定できないため定義しない（corpus の -1 / 21 は引き続き無名＝近似扱い）。
PROVISIONAL_EXECUTION_DELAYS: "dict[int, str]" = {
    ExecutionDelay.ZERO_LATENCY_IDEAL: "TBD-08",
    ExecutionDelay.DELAY_1MS: "TBD-20",
    ExecutionDelay.DELAY_5MS: "TBD-20",
    ExecutionDelay.DELAY_10MS: "TBD-20",
    ExecutionDelay.DELAY_20MS: "TBD-20",
    ExecutionDelay.DELAY_100MS: "TBD-20",
    ExecutionDelay.DELAY_500MS: "TBD-20",
    ExecutionDelay.DELAY_1000MS: "TBD-20",
}


def approximation_reason_for(delay: "int | None") -> "str | None":
    """遅延値 1 個の近似理由を返す（`None` なら近似ではない）。

    事前条件: なし（``None`` は「値が供給されていない」＝近似の主張をしない）。
    事後条件: 実証済みの値のみ ``None`` を返す。暫定の値は TBD 番号を含む理由を、
        名前を持たない値は未実測である旨の理由を返す。
    例外: 送出しない。``int`` と ``None`` のいずれに対しても値を返す（辞書引きは
        `dict.get` であり、未宣言の値でも `KeyError` にならない）。
    """
    if delay is None or delay in PROVEN_EXECUTION_DELAYS:
        return None
    tbd = PROVISIONAL_EXECUTION_DELAYS.get(delay)
    if tbd is not None:
        return f"delay={delay} 未実証（{tbd}）"
    return f"delay={delay} 未実測"


class OptimizationMode(IntEnum):
    """`Optimization`。0〜2 は corpus 実測。3 は暫定（TBD-04）。"""

    DISABLED = 0              # 実証（F-8）
    FULL_SLOW_COMPLETE = 1    # 実証（F-8: Full optimization）
    GENETIC = 2               # 実証（F-8: Genetic optimization）
    # 暫定（TBD-04）: MT5 UI の 4 番目「気配値表示で選択されたすべての銘柄」
    # （実測スクショ .doc/mt5_options/ss20260906204947.jpg）。0〜2 が既知で選択肢が
    # 4 つのため消去法で 3。保存 ini による生値実測は未取得。
    ALL_SYMBOLS_IN_MARKET_WATCH = 3


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


# ---------------------------------------------------------------------------
# MT5「設定」タブの表示ラベル（UI 専用の写像・`.ini` トークンには使わない）
# ---------------------------------------------------------------------------
# 実測源: `.doc/mt5_options/`（2026-09-06）。開いたドロップダウンのスクショ
# （ss20260906203328 / 204849 / 204918 / 204947）と保存 ini のファイル名。
# 未収載の値はラベル無し＝UI はメンバ名で出す（発明しない）。評価軸
# （OptimizationCriterion）と時間足のスクショは未取得のため写像を持たない。

#: `Dates` の表示ラベル（保存 ini のファイル名＝選択肢名が根拠）。
DATES_PRESET_UI_LABELS: "dict[DatesPreset, str]" = {
    DatesPreset.ENTIRE_HISTORY: "全履歴",
    DatesPreset.LAST_MONTH: "先月",
    DatesPreset.LAST_YEAR: "昨年",
}

#: `Dates` プリセット → 日付ボックスへ**表示**する解決期間の種別（表示専用・実行には使わない）。
#: MT5 はプリセット選択時に解決済み期間を不活性の From/To ボックスへ出す（実測）:
#:   - entire        = データ先頭〜データ最終日（ss20260906204651: 全履歴=2016.06.07〜2026.09.05）
#:   - year_to_date  = データ最終日の年の 1/1〜データ最終日（ss20260906204441 ＋
#:                     ss20260906195130: 昨年=2026.01.01〜2026.09.05・2 画面で一致）
#:   - month_to_date = データ最終日の月の 1 日〜データ最終日。**暫定**（TBD-21:
#:                     「先月」選択時のボックス表示は未採取。year_to_date との対称からの推定）
DATES_PRESET_RANGE_KINDS: "dict[DatesPreset, str]" = {
    DatesPreset.ENTIRE_HISTORY: "entire",
    DatesPreset.LAST_MONTH: "month_to_date",
    DatesPreset.LAST_YEAR: "year_to_date",
}

#: `ForwardMode` の表示ラベル（ss20260906203328: 開いたドロップダウンの実測）。
FORWARD_MODE_UI_LABELS: "dict[ForwardMode, str]" = {
    ForwardMode.DISABLED: "キャンセル",
    ForwardMode.SPLIT_HALF: "1/2",
    ForwardMode.SPLIT_THIRD: "1/3",
    ForwardMode.SPLIT_QUARTER: "1/4",
    ForwardMode.CUSTOM_DATE: "カスタム",
}

#: `ForwardMode` の分割比（分母）。フォワード期間＝設定期間の**後ろ** 1/n（MT5 公式ヘルプ
#: 「フォワード期間は設定期間を選択比で分割した後半部」＋保存 ini ファイル名で n の対応を
#: 実証・2026-09-06）。**表示専用**——分割選択時 `ForwardDate` は `.ini` に書かれない
#: （F-10）ため、この比から求めた分割日は投入本文に載せない。日単位の丸め規則は MT5
#: 未実測（暫定: フォワード日数 = floor(期間日数 / 分母)。VM 実測で確定したら front の
#: `computeForwardSplitDate` 1 箇所を直す）。
FORWARD_MODE_SPLIT_DENOMINATORS: "dict[ForwardMode, int]" = {
    ForwardMode.SPLIT_HALF: 2,
    ForwardMode.SPLIT_THIRD: 3,
    ForwardMode.SPLIT_QUARTER: 4,
}

#: `Model` の表示ラベル（ss20260906204918: 開いたドロップダウンの実測）。
#: 値との対応: 0/1/2/4 は corpus コメント（every tick / m1 ohlc / open prices /
#: real ticks）と表示文言の対で確定。3 は残る 1 語「数値計算」＝消去法（TBD-01 のまま）。
TICK_MODEL_UI_LABELS: "dict[TickModel, str]" = {
    TickModel.EVERY_TICK: "全ティック",
    TickModel.ONE_MINUTE_OHLC: "1分足 OHLC",
    TickModel.OPEN_PRICES_ONLY: "始値のみ",
    TickModel.MATH_CALCULATIONS: "数値計算",
    TickModel.REAL_TICKS: "リアルティックに基づいたすべてのティック",
}

#: `Optimization` の表示ラベル（ss20260906204947: 開いたドロップダウンの実測）。
OPTIMIZATION_MODE_UI_LABELS: "dict[OptimizationMode, str]" = {
    OptimizationMode.DISABLED: "無効",
    OptimizationMode.FULL_SLOW_COMPLETE: "完全アルゴリズム(遅い)",
    OptimizationMode.GENETIC: "遺伝的アルゴリズム(速い)",
    OptimizationMode.ALL_SYMBOLS_IN_MARKET_WATCH: "気配値表示で選択されたすべての銘柄",
}

#: `ExecutionMode`（延滞）の表示ラベル（ss20260906204849: 開いたドロップダウンの実測）。
#: 数値ラベル⇔生値の対応は 50 のみ実証・他は暫定（TBD-20。PROVISIONAL 側の宣言を参照）。
EXECUTION_DELAY_UI_LABELS: "dict[int, str]" = {
    ExecutionDelay.ZERO_LATENCY_IDEAL: "遅延ゼロ、理想的な実行",
    ExecutionDelay.DELAY_1MS: "1ミリ秒",
    ExecutionDelay.DELAY_5MS: "5ミリ秒",
    ExecutionDelay.DELAY_10MS: "10ミリ秒",
    ExecutionDelay.DELAY_20MS: "20ミリ秒",
    ExecutionDelay.DELAY_50MS: "50ミリ秒",
    ExecutionDelay.DELAY_100MS: "100ミリ秒",
    ExecutionDelay.DELAY_500MS: "500ミリ秒",
    ExecutionDelay.DELAY_1000MS: "1000ミリ秒",
}
