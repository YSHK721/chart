"""Mt5ReportSpecDerivation: MT5 レポート（``expected/report.json``）から銘柄仕様を機械導出する。

由来（ISSUE-445・`.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md` 段階 0）:
    `case.yaml` の ``symbol:`` ブロックは「銘柄仕様 (実 MT5 由来の確定値)」と名乗るが、
    MT5 レポート（xlsx ``Settings`` 8 項目 / ``tester.log`` / ``report.json``）に
    ``contract_size`` の記載は**一度も無い**（実測 2026-08-25）。人が書いた値と供給元を
    突き合わせる機構が無かったため、``contract_size=10``（真値 1.0）が 2 か月以上検出
    されなかった。本モジュールは**供給元と独立な証拠**である実行結果（deals）から
    仕様を機械導出し、人が書いた値を照合可能にする。

導出できるものだけを導出する（憶測禁止）:
    ``contract_size``      : 決済 deal の ``profit == Δprice × sign × volume × contract_size``
                             から**整合性の片側検査**として判定する（下記「なぜ逆算しないか」）。
    ``executed_volume``    : deals の ``vol``（＝実際に約定したロット。EA 入力値ではない）。
    ``price_decimals``     : deals の ``price`` に観測される小数桁の最大（``digits`` の下限）。
    ``account_leverage``   : ``settings.leverage``（``"1:10"``）。**口座属性**であり銘柄仕様ではない。
    ``settlement_currency``: ``settings.currency``。

導出できないもの（本モジュールは扱わない）:
    ``volume_min`` / ``volume_step`` / ``volume_max`` / ``stops_level``。
    レポートは制約を出力せず、約定結果からは制約を一意に復元できない。導出できないものを
    導出したふりをしない。これらの権威は供給元スナップショット（段階 2）に限る。

なぜ ``contract_size`` を「逆算」ではなく「照合」で扱うか:
    MT5 は ``profit`` を口座通貨の精度（JPY = 0 桁）へ丸めて出力する（実測: 決済 1163 件のうち
    4 件で ``Δprice`` と ``profit`` が 0.2〜0.5 ずれる。例 32.4 → 32 / 9.5 → 10）。丸めた値からの
    除算は真値を一意に定めず、``contract_size`` は区間としてしか同定できない。よって
    :func:`contract_size_consistency` は「与えられた値が全 deal の丸め許容内に収まるか」を
    判定する（片側検査）。参考値として :func:`contract_size_estimate` が比の中央値を返すが、
    これは失敗メッセージ用であり判定には使わない。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from decimal import Decimal

# 浮動小数の比較許容（価格差 × ロットの積で生じる丸め誤差を吸収する）。
_EPS = 1e-6

_SIGN = {"buy": 1.0, "sell": -1.0}


@dataclass(frozen=True)
class ClosedTrade:
    """決済まで完了した往復トレード 1 件（in deal と out deal の対）。"""

    side: str
    entry_price: float
    exit_price: float
    volume: float
    profit: float

    def price_delta(self) -> float:
        """建値からの価格差（買いは上昇が正・売りは下落が正）。"""
        return (self.exit_price - self.entry_price) * _SIGN[self.side]


@dataclass(frozen=True)
class Mismatch:
    """照合に失敗した 1 件（失敗メッセージ用の証拠）。"""

    trade: ClosedTrade
    expected_profit: float
    residual: float


@dataclass(frozen=True)
class ConsistencyReport:
    """``contract_size`` 照合の結果。"""

    value: float
    ok: bool
    checked: int
    mismatches: "tuple[Mismatch, ...]"
    worst_residual: float
    tolerance: float

    def describe(self) -> str:
        """失敗時に読める要約（期待値をテスト側にリテラルで持たせないため本体が説明する）。"""
        head = (
            f"contract_size={self.value!r}: {self.checked} 件中 "
            f"{len(self.mismatches)} 件が丸め許容 {self.tolerance!r} を超過"
            f"（最大残差 {self.worst_residual!r}）"
        )
        lines = [head]
        for m in self.mismatches[:3]:
            t = m.trade
            lines.append(
                f"  {t.side} {t.entry_price!r} -> {t.exit_price!r} vol={t.volume!r}: "
                f"report profit={t.profit!r} / 期待 {m.expected_profit!r} / 残差 {m.residual!r}"
            )
        if len(self.mismatches) > 3:
            lines.append(f"  … 他 {len(self.mismatches) - 3} 件")
        return "\n".join(lines)


def _trade_deals(expected: dict) -> "list[dict]":
    """残高計上（``type == "balance"``）を除いた約定 deal 列。"""
    return [d for d in expected["deals"] if d["type"] != "balance"]


def closed_trades(expected: dict) -> "list[ClosedTrade]":
    """``dir`` の in/out を対にして往復トレードへ復元する。

    決済 deal（``dir == "out"``）が保持する ``profit`` が損益の実測値であり、建玉 deal
    （``dir == "in"``）の ``profit`` は 0 である（実測）。
    """
    trades: list[ClosedTrade] = []
    opened: dict | None = None
    for d in _trade_deals(expected):
        if d["dir"] == "in":
            opened = d
        elif d["dir"] == "out" and opened is not None:
            trades.append(
                ClosedTrade(
                    # 建玉側の type が売買方向（決済 deal は反対方向で記録される）。
                    side=opened["type"],
                    entry_price=float(opened["price"]),
                    exit_price=float(d["price"]),
                    volume=float(d["vol"]),
                    profit=float(d["profit"]),
                )
            )
            opened = None
    return trades


def executed_volumes(expected: dict) -> "frozenset[float]":
    """実際に約定したロットの集合（EA 入力の ``Lot`` ではない）。

    参照実装 ``MA_Slope_EA.mq5:NormalizeLot()`` は ``SYMBOL_VOLUME_MIN/STEP/MAX`` を実行時に
    読んで入力ロットを丸め・下限まで持ち上げる。よってレポートの ``vol`` は入力値と一致
    するとは限らない（本 fixture では入力 0.1 に対し約定 1.0・実測）。
    """
    return frozenset(float(d["vol"]) for d in _trade_deals(expected))


def _decimals(value: float) -> int:
    """末尾 0 を除いた小数桁数（``39412.0`` → 0 / ``38325.7`` → 1）。"""
    exponent = Decimal(repr(value)).normalize().as_tuple().exponent
    return -int(exponent) if int(exponent) < 0 else 0


def price_decimals(expected: dict) -> int:
    """deals の ``price`` に観測される小数桁の最大（``digits`` の**下限**）。

    全価格がたまたま整数だった場合は過小評価する。よって ``digits`` の判定は
    「観測桁 <= 申告 digits」の片側検査に留める（等値検査に使わない）。
    """
    return max(_decimals(float(d["price"])) for d in _trade_deals(expected))


def profit_decimals(expected: dict) -> int:
    """deals の ``profit`` に観測される小数桁の最大（＝口座通貨の丸め桁）。"""
    return max(_decimals(float(d["profit"])) for d in _trade_deals(expected))


def rounding_tolerance(expected: dict) -> float:
    """``profit`` の丸めが生む最大残差（丸め幅の半分）。"""
    return 0.5 * 10.0 ** (-profit_decimals(expected)) + _EPS


def contract_size_consistency(expected: dict, value: float) -> ConsistencyReport:
    """``value`` が全決済 deal の損益と丸め許容内で整合するかを判定する（片側検査）。"""
    tol = rounding_tolerance(expected)
    trades = closed_trades(expected)
    mismatches: list[Mismatch] = []
    worst = 0.0
    for t in trades:
        expected_profit = t.price_delta() * t.volume * value
        residual = abs(expected_profit - t.profit)
        worst = max(worst, residual)
        if residual > tol:
            mismatches.append(
                Mismatch(trade=t, expected_profit=expected_profit, residual=residual)
            )
    return ConsistencyReport(
        value=float(value),
        ok=not mismatches,
        checked=len(trades),
        mismatches=tuple(mismatches),
        worst_residual=worst,
        tolerance=tol,
    )


def contract_size_estimate(expected: dict) -> float:
    """``profit / (Δprice × volume)`` の中央値（**参考値**・判定には使わない）。

    丸めの影響を受けるため一意ではない。失敗メッセージで「実測はどのあたりか」を示す用途に限る。
    """
    ratios = [
        t.profit / (t.price_delta() * t.volume)
        for t in closed_trades(expected)
        if abs(t.price_delta() * t.volume) > _EPS
    ]
    if not ratios:
        raise ValueError("比を取れる決済 deal が 1 件も無い")
    return statistics.median(ratios)


def account_leverage(expected: dict) -> float:
    """``settings.leverage``（``"1:10"``）→ ``10.0``。

    **口座属性であり銘柄仕様ではない**（``mt5.symbol_info`` に ``leverage`` は無く、
    ``tester.log`` も ``initial deposit 10000 JPY, leverage 1:10`` と口座の行に記録する・実測）。
    """
    raw = str(expected["settings"]["leverage"])
    left, _, right = raw.partition(":")
    if not right or left.strip() != "1":
        raise ValueError(f"leverage の表記が '1:N' でない: {raw!r}")
    return float(right)


def settlement_currency(expected: dict) -> str:
    """``settings.currency``（本 fixture は口座通貨＝決済通貨のケース）。"""
    return str(expected["settings"]["currency"])
