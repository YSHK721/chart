"""指標供給・因果性検定のプレーン DTO と usecase 例外（CLEAN_ARCH §5・Phase 3 F-5）。

境界を跨ぐデータは全て**プレーン**（dataclass / Mapping）にする。pandas の Series・
indicator_ui の応答 dict・pathlib の Path は usecase へ入れない（adapter に留める）。

契約改訂（2026-08-11 裁定 A/C・実測起因）:
    * 系列は**束（bundle）**で運ぶ。1 指標は 1 回の計算で全系列を返すため、系列ごとに
      計算を呼ぶと同じ計算を系列数ぶん重複して払う（実測: 母集合 26 組 → 122 系列・
      1 パス 138.5 秒）。:class:`SeriesBundle` / :data:`TailBundle` がその単位である。
    * **選択可否の単位は「系列」**（戦略は系列名で指標値を参照する）。供給コストは
      1 回の計算＝指標単位で決まるため、コストで落ちた指標はその全系列が落ちる。
    * 値の欠測は ``None`` に正規化する（NaN を運ばない）。``None`` 同士は一致。
    * 選択不可の理由（``reason``）は 3 値固定。自由文は ``detail`` へ分ける。
      機械判定（一覧の絞り込み・再検定の要否）が自由文の表記ゆれに依存しなくなる。
"""
from __future__ import annotations

from collections.abc import Mapping as _MappingBase
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

#: 案 i と案 ii の値がずれた（＝供給に使うと戦略が違う値を掴む）。
REASON_MISMATCH = "mismatch"
#: 供給（案 ii の 1 回計算）が予算（通過条件 3 = 1 秒 / 供給窓）を超えた。
REASON_SUPPLY_COST_EXCEEDED = "supply_cost_exceeded"
#: 検定を最後まで実行できなかった（予算超過・比較不能・整列不能）。
#:   「値がずれた」（:data:`REASON_MISMATCH`）とは**別事象**である。混ぜると台帳に
#:   誤った原因が残り、再検定の判断ができなくなる。
REASON_VERIFICATION_INCOMPLETE = "verification_incomplete"

#: 選択不可の理由として許す値（これ以外は :class:`CausalityFinding` が拒否する）。
REASONS = (
    REASON_MISMATCH,
    REASON_SUPPLY_COST_EXCEEDED,
    REASON_VERIFICATION_INCOMPLETE,
)


@dataclass(frozen=True)
class IndicatorSpec:
    """検定・供給の対象を一意に決める申告。

    ``indicator`` / ``variant`` は indicator_ui の compute_id / variant（`GET /catalog` の
    単一情報源に由来する）。``params`` は当該 variant が受理する param の集合。
    """

    indicator: str
    variant: str
    params: "Mapping[str, Any]" = field(default_factory=dict)

    @property
    def key(self) -> str:
        """台帳・応答で使う指標の識別子。"""
        return f"{self.indicator}:{self.variant}"


@dataclass(frozen=True)
class SeriesPoint:
    """指標系列の 1 点。

    ``time``: UNIX 秒。``value``: 値（**未定義点は ``None``**）。compute は未定義区間を
    NaN でも null でも返し得るため、境界を跨ぐ前に ``None`` へ正規化する（NaN は
    ``NaN != NaN`` のため、突合規則を「値の比較」だけで書けなくなる）。
    """

    time: int
    value: "float | None"


class SeriesNameCollisionError(Exception):
    """1 指標の中で系列名が重複した（無音上書き禁止）。

    後勝ちで黙って上書きすると、戦略が別系列の値を掴んだまま完走する
    （エラーにならずに誤った結果を返す）。
    """


class SeriesBundle(_MappingBase):
    """1 回の計算で得た系列束（系列名 → 点列）と、供給対象外だった系列名。

    ``Mapping[str, list[SeriesPoint]]`` として振る舞う（``bundle["MA"]`` / ``in`` /
    ``keys()`` / 辞書との ``==``）。同一概念に 2 つの型を作らないため、除外系列名も
    この型が持つ（台帳へ記録する義務があり、再計算して取り直すと供給コストを二重に払う）。

    ``excluded``: 時系列でない kind（``horizontal_line`` 等）のため供給対象から外した
    系列名。「指標にその系列が無い」と「対象 kind でないので供給しない」を区別する。

    不変条件: ``excluded`` と系列名は**交わらない**。同じ名前で対象 kind の pane と
    対象外の pane が同時に返ることがあり（2026-08-11 実測: ma_marod は同名で線と
    塗りの 2 pane）、両方に載せると台帳に同じ系列の行が 2 つ（判定は別）できる。
    供給できる pane が 1 つでもあればその名前は「供給できる」。
    """

    __slots__ = ("_series", "excluded")

    def __init__(
        self,
        series: "Mapping[str, list[SeriesPoint]] | Iterable[tuple[str, list[SeriesPoint]]]",
        excluded: "Iterable[str]" = (),
    ) -> None:
        pairs = series.items() if hasattr(series, "items") else series
        table: "dict[str, list[SeriesPoint]]" = {}
        for name, points in pairs:
            key = str(name)
            if key in table:
                raise SeriesNameCollisionError(f"系列名が重複しています: {key}")
            table[key] = list(points)
        self._series = table
        # 供給できる pane を持つ名前は excluded から外す（不変条件）。
        self.excluded = tuple(
            dict.fromkeys(str(name) for name in excluded if str(name) not in table)
        )

    def __getitem__(self, name: str) -> "list[SeriesPoint]":
        return self._series[name]

    def __iter__(self):
        return iter(self._series)

    def __len__(self) -> int:
        return len(self._series)

    def __repr__(self) -> str:
        return f"SeriesBundle({self._series!r}, excluded={self.excluded!r})"


#: 案 i の 1 回計算から得る「各系列の末尾点」（点が無い系列は ``None``）。
#:   キー集合は同条件の :class:`SeriesBundle` の**部分集合**（窓が短いうちは compute が
#:   系列そのものを返さない）。上位集合になったら比較不能（同じものを比べていない）。
TailBundle = Mapping[str, "SeriesPoint | None"]


@dataclass(frozen=True)
class SupplyCost:
    """供給（案 ii の 1 回計算）の実測。

    ``seconds``: :meth:`~simulator.sim_ui.usecase.indicator_ports.CausalSeriesProbePort.series_full`
      1 回の所要秒。通過条件 3（供給窓 1 万本で 1 秒以内）の証拠そのもの。
    ``bundle``: その計算で得た系列束。捨てて測り直すと供給コストを二重に払うため、
      測定結果と一緒に運ぶ。
    """

    seconds: float
    bundle: SeriesBundle


@dataclass(frozen=True)
class CausalityFinding:
    """**1 系列**の検定結果（台帳 1 行）。

    ``selectable``: 案 ii（全期間 1 回計算）の供給を sim モードで使ってよいか。
    ``reason``: 使えない理由。:data:`REASONS` の 3 値固定（使えるときは ``None``）。
    ``detail``: 人が読むための補足（自由文）。機械判定には使わない。
    ``bars_compared``: 案 i と案 ii の**両方が値を持ち**突合したバー数。
    ``warmup_bars``: 検定窓の先頭で、案 ii だけが値を持ったバー数（案 i がまだ値を出せる
      本数に達していない区間）。供給窓の先頭この本数ぶんは「案 i では出せない値」である
      という事実をそのまま残す（0 に丸めない）。
    ``supply_seconds``: 供給 1 回の所要秒（通過条件 3 の証拠）。
    """

    spec: IndicatorSpec
    series_name: str
    selectable: bool
    reason: "str | None" = None
    detail: "str | None" = None
    bars_compared: int = 0
    warmup_bars: int = 0
    max_abs_diff: "float | None" = None
    first_mismatch_time: "int | None" = None
    supply_seconds: "float | None" = None

    def __post_init__(self) -> None:
        # 3 値固定を宣言でなく機械的に強制する（自由文が混ざると一覧の絞り込みが壊れる）。
        if self.selectable and self.reason is not None:
            raise ValueError("選択可能な系列に reason を付けられません")
        if not self.selectable and self.reason not in REASONS:
            raise ValueError(f"reason は {REASONS} のいずれかです: {self.reason!r}")


@dataclass(frozen=True)
class LedgerConditions:
    """台帳が主張する測定条件。条件を書き残さない一致主張は再現できない。

    ``supply_bars``: 供給窓（案 ii を 1 回計算する窓）のバー本数。
    ``verify_bars``: 段 1（スクリーニング）の検定窓のバー本数。
    ``verify_coverage``: 選択可能と記録した系列が検定された範囲 ÷ 供給窓
      （段 2 まで到達した系列は 1.0）。
    ``timeout``: 1 指標あたりの検定予算（秒・``None`` は無制限）。
    ``supply_budget``: 供給コストの上限（秒）。通過条件 3 の閾値。
    """

    ref: str
    timeframe: "str | None"
    supply_bars: int
    verify_bars: int
    verify_coverage: float = 1.0
    timeout: "float | None" = None
    supply_budget: float = 1.0
    limit: "int | None" = None
    tolerance: float = 0.0
    probe_mode: str = "full"


@dataclass(frozen=True)
class LedgerSnapshot:
    """因果性台帳の全体（schema 版・測定時刻・測定条件・系列ごとの結果）。"""

    schema: int
    measured_at: str
    conditions: LedgerConditions
    findings: "tuple[CausalityFinding, ...]" = ()


@dataclass(frozen=True)
class IndicatorListingItem:
    """UC-S4 の応答 1 件（＝1 系列。不一致系列も ``selectable=False`` で必ず含める）。"""

    indicator: str
    variant: str
    params: "Mapping[str, Any]"
    series_name: str
    selectable: bool
    reason: "str | None" = None
    detail: "str | None" = None


@dataclass(frozen=True)
class IndicatorListing:
    """UC-S4 の応答全体（測定条件つき）。"""

    measured_at: str
    conditions: LedgerConditions
    items: "tuple[IndicatorListingItem, ...]" = ()


class SeriesAlignmentError(Exception):
    """系列点列をバー時刻列へ整列できない（有効区間の欠測・時間軸不一致）。"""


class CausalityComparisonError(Exception):
    """案 i と案 ii を突合できない（片側にのみ存在する時刻・系列＝比較の前提が不成立）。

    「不一致」と区別する。値がずれているのではなく**比較の前提が成立していない**。
    同じ扱いにすると、原因の違う 2 つの事象が台帳で同じ顔になる。
    """


class CausalityLedgerUnavailableError(Exception):
    """因果性台帳を読めない（不在・schema 不一致）。

    fail-closed: 台帳が無いときに「全指標が選択可能」とも「空一覧」とも答えない。
    どちらも「検定していない指標を使ってよい」という誤りを黙って通す。
    """


class IndicatorCatalogUnavailableError(Exception):
    """検定対象母集合（indicator_ui の catalog）を取得できない。"""


class IndicatorSupplyError(Exception):
    """指標系列をレジストリへ供給できない（未検定系列の要求など）。"""
