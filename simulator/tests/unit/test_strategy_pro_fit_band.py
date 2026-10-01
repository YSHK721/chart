"""adapter/strategy/pro_fit_band.py の PRO!fit_Band 戦略テスト（SPEC §3.5・PROCESS §3.4）.

戦略（#5 PRO!fit_Band ＝ my_first_ea・SPEC §3.5 — EMA 傾き + ADX + DI）:
    指標参照（PROCESS §0.3: MQL [0]=最新 → Python iloc[bar_index], [1]→bar_index-1, [2]→bar_index-2）:
        EMA(MA_Period,close) 3 点, ADX(ADX_Period) 本線, +DI, −DI.
        p_close = mrate[1].close（1 本前の確定終値）= close.iloc[bar_index-1].
    エントリ（全条件 AND・厳密不等号）:
        買い: EMA[0]>EMA[1] && EMA[1]>EMA[2]（上昇）& p_close>EMA[1] & ADX[0]>Adx_Min & +DI[0]>−DI[0]
        売り: EMA[0]<EMA[1] && EMA[1]<EMA[2]（下降）& p_close<EMA[1] & ADX[0]>Adx_Min & +DI[0]<−DI[0]
    決済: 発注時の固定 SL/TP のみ.
        桁補正: _Digits∈{3,5} のとき STP=StopLoss*10, TKP=TakeProfit*10.
        買い: sl=Ask−STP×point, tp=Ask+TKP×point ／ 売り: sl=Bid+STP×point, tp=Bid−TKP×point.
    制限: 同方向ポジ保有時は重複禁止（PositionSelect・反対方向の扱いは原典踏襲）.
    実行頻度: 新規バー 1 回（on_new_bar が新規バーごとに 1 回呼ばれる前提）.

ポート契約（usecase StrategyPort・tc24051901 と同形）:
    on_new_bar(bar_index, indicators, account) -> list[Order]
    - エントリ基準価格は indicators.get("close").iloc[bar_index]（spread=0 近似で Ask=Bid=close）.
    - Order は kind="market"・price=None（約定価格は execution で解決）・sl/tp は絶対価格.

一次情報（原典オラクル）:
    本テスト末尾の ``_oracle_order`` は原典 MQL5 ソース
    ``simulator/experts/PRO!fit_Band.mq5``（MetaQuotes "My First EA" 原型・#5 の原典）の
    OnTick スカラー条件・SL/TP 式・桁補正・Bars ゲート・同方向のみ抑止を、production
    （pro_fit_band.py）を一切 import せずに Python で独立に書き下したオラクルである.
    ``test_order_matches_mql5_source_oracle`` が合成系列バッテリに対し ProFitBand の
    発注（side/sl/tp/volume）とオラクルの一致を固定する（実装非依存＝トートロジー回避）.
    非トートロジー性は「実装の SL/TP 符号を 1 箇所反転するとオラクル一致が崩れる」ことを
    開発時ミューテーション（TDD output §3）で実証済み.

TDD AAA 構造. F.I.R.S.T.
"""
from __future__ import annotations

import pandas as pd
import pytest

from simulator.domain.order import Order
from simulator.usecase.ports import StrategyPort


# SPEC §3.5 入力: StopLoss=30, TakeProfit=100, ADX_Period=8, MA_Period=8,
# Adx_Min=22.0, Lot=0.1. point_size と digits は決定論固定（PROCESS §7-#7）.
# warmup は原典どおり固定 60（`Bars<60`・input 無し・ISSUE-556）。条件式の検証は、3 点の系列の前に
# 先頭値を _PAD 本足して現足を _NOW（=60 本目より後）に置き、Bars<60 ゲートの外で行う。
# 条件式が読むのは [0]/[1]/[2] だけなので、前に足した本は結果に関わらない。
_CONFIG = {
    "lot_size": 0.1,
    "stop_loss_points": 30,
    "take_profit_points": 100,
    "adx_min": 22.0,
    "point_size": 0.0001,
    "digits": 5,  # 5 桁 → STP/TKP ×10（SPEC §3.5 桁補正）
}
_PAD = 59
_NOW = 2 + _PAD  # 3 点系列の末尾（元の bar_index=2）


def _padded(xs):
    return [xs[0]] * _PAD + list(xs)


class _Account:
    def __init__(self, sides):
        self.open_positions = [type("P", (), {"side": s})() for s in sides]


def _registry(ema, adx, plus_di, minus_di, close):
    """3 点系列の前に _PAD 本足した registry（現足は _NOW）。"""
    return _raw_registry(
        _padded(ema), _padded(adx), _padded(plus_di), _padded(minus_di), _padded(close)
    )


def _raw_registry(ema, adx, plus_di, minus_di, close):
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    return PandasIndicatorRegistry(
        {
            "ema": pd.Series(ema),
            "adx": pd.Series(adx),
            "plus_di": pd.Series(plus_di),
            "minus_di": pd.Series(minus_di),
            "close": pd.Series(close),
        }
    )


# --- LSP -------------------------------------------------------------------

def test_pro_fit_band_implements_strategy_port():
    # Arrange / Act
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()

    # Assert: StrategyPort のサブクラスで抽象解決済み
    assert isinstance(strat, StrategyPort)


# --- TD.1 正常系: 買い条件 AND 充足 -----------------------------------------

def test_uptrend_all_conditions_met_opens_buy_with_digit_corrected_sltp():
    # Arrange: EMA[0]>EMA[1]>EMA[2] 上昇, p_close(=close[1])>EMA[1], ADX[0]>22, +DI[0]>−DI[0]
    # index2 が現足(bar_index=2): EMA=[..,1.0,1.1,1.2]→1.2>1.1>1.0 上昇.
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _registry(
        ema=[1.0, 1.1, 1.2],
        adx=[10.0, 20.0, 25.0],            # ADX[0]=index2=25>22
        plus_di=[10.0, 20.0, 30.0],        # +DI[0]=30 > −DI[0]=10
        minus_di=[20.0, 15.0, 10.0],
        close=[1.2000, 1.2010, 1.2020],    # p_close=close[1]=1.2010 > EMA[1]=1.1（真）
    )
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(_NOW, ind, _Account([]))

    # Assert: 買い成行・桁補正(×10) 後の SL/TP（price=close[2]=1.2020）
    assert len(orders) == 1
    o = orders[0]
    assert isinstance(o, Order)
    assert o.side == "buy" and o.kind == "market" and o.price is None
    assert o.volume == 0.1
    assert o.sl == 1.2020 - (30 * 10) * 0.0001   # STP×10
    assert o.tp == 1.2020 + (100 * 10) * 0.0001  # TKP×10


# --- TD.1 正常系: 売り条件 AND 充足 -----------------------------------------

def test_downtrend_all_conditions_met_opens_sell_with_digit_corrected_sltp():
    # Arrange: EMA[0]<EMA[1]<EMA[2] 下降, p_close(=close[1])<EMA[1], ADX[0]>22, +DI[0]<−DI[0]
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _registry(
        ema=[1.2, 1.1, 1.0],               # 1.0<1.1<1.2 下降
        adx=[10.0, 20.0, 25.0],
        plus_di=[20.0, 15.0, 10.0],        # +DI[0]=10 < −DI[0]=30
        minus_di=[10.0, 20.0, 30.0],
        close=[1.3000, 1.0500, 1.2990],    # p_close=close[1]=1.0500 < EMA[1]=1.1（真）
    )
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(_NOW, ind, _Account([]))

    # Assert: 売り成行・桁補正後 SL/TP（price=close[2]=1.2990）
    assert len(orders) == 1
    o = orders[0]
    assert o.side == "sell" and o.kind == "market" and o.price is None
    assert o.sl == 1.2990 + (30 * 10) * 0.0001
    assert o.tp == 1.2990 - (100 * 10) * 0.0001


# --- TD.4 条件不成立: ADX <= Adx_Min（厳密不等号）---------------------------

def test_uptrend_but_adx_not_above_min_returns_no_order():
    # Arrange: 上昇トレンド・DI も買い向きだが ADX[0]=22.0 は >22 でない（厳密不等号）
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _registry(
        ema=[1.0, 1.1, 1.2],
        adx=[10.0, 20.0, 22.0],            # ADX[0]=22.0 ＝ Adx_Min（> でないので不成立）
        plus_di=[10.0, 20.0, 30.0],
        minus_di=[20.0, 15.0, 10.0],
        close=[1.2000, 1.2010, 1.2020],
    )
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(_NOW, ind, _Account([]))

    # Assert
    assert orders == []


# --- TD.4 条件不成立: DI の大小が買い向きでない -----------------------------

def test_uptrend_but_plus_di_not_above_minus_di_returns_no_order():
    # Arrange: 上昇・ADX>22 だが +DI[0]<=−DI[0]（買い不成立）
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _registry(
        ema=[1.0, 1.1, 1.2],
        adx=[10.0, 20.0, 25.0],
        plus_di=[10.0, 20.0, 15.0],        # +DI[0]=15 < −DI[0]=20
        minus_di=[5.0, 10.0, 20.0],
        close=[1.2000, 1.2010, 1.2020],
    )
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(_NOW, ind, _Account([]))

    # Assert
    assert orders == []


# --- TD.4 条件不成立: EMA が単調上昇でない（傾き条件）-----------------------

def test_ema_not_monotonic_up_returns_no_order():
    # Arrange: EMA[0]>EMA[1] だが EMA[1]<=EMA[2]（単調上昇でない）→ 買い不成立
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _registry(
        ema=[1.15, 1.1, 1.2],              # EMA[1]=1.1 < EMA[2]=1.15（[1]>[2] でない）
        adx=[10.0, 20.0, 25.0],
        plus_di=[10.0, 20.0, 30.0],
        minus_di=[20.0, 15.0, 10.0],
        close=[1.2000, 1.2010, 1.2020],
    )
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(_NOW, ind, _Account([]))

    # Assert
    assert orders == []


# --- TD.4 条件不成立: p_close と EMA[1] の位置（buy で p_close<=EMA[1]）------

def test_uptrend_but_prev_close_not_above_ema1_returns_no_order():
    # Arrange: 上昇・ADX・DI は買い向きだが p_close(=close[1])<=EMA[1]
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _registry(
        ema=[1.0, 1.1, 1.2],
        adx=[10.0, 20.0, 25.0],
        plus_di=[10.0, 20.0, 30.0],
        minus_di=[20.0, 15.0, 10.0],
        close=[1.2000, 1.0500, 1.2020],    # p_close=close[1]=1.0500 < EMA[1]=1.1（buy 不成立）
    )
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(_NOW, ind, _Account([]))

    # Assert
    assert orders == []


# --- TD.2 境界値: EMA[2] 不在の足も Bars<60 ゲートが含む → 発注なし ------------

def test_bars_gate_covers_missing_two_prior_ema():
    # Arrange: 2 点だけの系列で bar_index=1 は [2]（2 本前）が無い。専用の検査は無く、
    #          Bars<60 ゲート（原典 L129）がこの足を含む（ISSUE-556）。
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _raw_registry(
        ema=[1.0, 1.1],
        adx=[10.0, 25.0],
        plus_di=[10.0, 30.0],
        minus_di=[20.0, 10.0],
        close=[1.2000, 1.2010],
    )
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(1, ind, _Account([]))

    # Assert
    assert orders == []


# --- 状態: 同方向ポジ保有は重複禁止（SPEC §3.5 制限）------------------------

def test_same_side_position_blocks_duplicate_buy():
    # Arrange: 買い条件成立だが既に buy 保有中 → 重複禁止
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _registry(
        ema=[1.0, 1.1, 1.2],
        adx=[10.0, 20.0, 25.0],
        plus_di=[10.0, 20.0, 30.0],
        minus_di=[20.0, 15.0, 10.0],
        close=[1.2000, 1.2010, 1.2020],
    )
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(_NOW, ind, _Account(["buy"]))

    # Assert
    assert orders == []


# --- 境界: digits が 3/5 以外なら桁補正なし（×1）----------------------------

def test_non_3_5_digits_uses_uncorrected_sltp():
    # Arrange: digits=4（3/5 以外）→ STP/TKP は ×10 されない（SPEC §3.5 桁補正条件）
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    cfg = dict(_CONFIG, digits=4)
    strat = ProFitBand()
    ind = _registry(
        ema=[1.0, 1.1, 1.2],
        adx=[10.0, 20.0, 25.0],
        plus_di=[10.0, 20.0, 30.0],
        minus_di=[20.0, 15.0, 10.0],
        close=[1.2000, 1.2010, 1.2020],
    )
    strat.on_init(cfg, ind)

    # Act
    orders = strat.on_new_bar(_NOW, ind, _Account([]))

    # Assert: 補正なし（×1）
    o = orders[0]
    assert o.sl == 1.2020 - 30 * 0.0001
    assert o.tp == 1.2020 + 100 * 0.0001


# --- TD.2 境界値: Bars<60 warmup ゲート（SPEC §3.5 / PROCESS §2-A・§3.4 step1）----
# 原典 L129 の固定 60（input 無し・ISSUE-556）。
# MQL `Bars` は現足[0]を含む総本数 → 現足が bar_index のとき総本数 = bar_index+1。
# Bars<60 ⟺ bar_index+1<60 ⟺ bar_index<59。ちょうど60本目(bar_index=59)で発注可。


def _long_buy_registry(n=70):
    """買い条件を全バーで満たす長さ n の決定論系列（warmup ゲート検証用）。

    EMA は単調上昇（EMA[0]>EMA[1]>EMA[2]）、ADX>22、+DI>−DI、p_close>EMA[1] を全 index で充足。
    """
    ema = [1.0 + 0.01 * i for i in range(n)]
    adx = [25.0] * n
    plus_di = [30.0] * n
    minus_di = [10.0] * n
    # p_close = close[bar_index-1] を EMA[bar_index-1] より十分上に置く（buy 位置条件）
    close = [e + 1.0 for e in ema]
    return _raw_registry(ema=ema, adx=adx, plus_di=plus_di, minus_di=minus_di, close=close)


def test_within_warmup_returns_no_order_even_when_buy_conditions_met():
    # Arrange: 買い条件成立だが warmup 内（bar_index=58 → Bars=59<60）→ 発注禁止
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _long_buy_registry(70)
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(58, ind, _Account([]))

    # Assert: Bars=59<60 → 処理しない
    assert orders == []


def test_exactly_60th_bar_allows_order_when_buy_conditions_met():
    # Arrange: ちょうど 60 本目（bar_index=59 → Bars=60、<60 でない）で発注可
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _long_buy_registry(70)
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(59, ind, _Account([]))

    # Assert: warmup 経過 → 買い発注 1 件
    assert len(orders) == 1
    assert orders[0].side == "buy"


def test_warmup_is_fixed_60_and_config_min_bars_is_not_read():
    # Arrange: 原典に min_bars の input は無い（ISSUE-556）。config に min_bars=5 を載せても
    #          ゲートは 60 のまま。config は subscript だけで読む（.get を持たない型でも走る）。
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    cfg = dict(_CONFIG, min_bars=5)
    strat = ProFitBand()
    ind = _long_buy_registry(70)
    strat.on_init(cfg, ind)

    # Act
    blocked = strat.on_new_bar(4, ind, _Account([]))

    # Assert
    assert blocked == []


class _SubscriptOnlyConfig:
    """RunConfig と同じ契約: 戦略パラメータは subscript だけで公開し、get を持たない。"""

    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]


def test_runs_with_subscript_only_config():
    # Arrange: ISSUE-556 の再現形（config が get を持たない）
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _long_buy_registry(70)
    strat.on_init(_SubscriptOnlyConfig(_CONFIG), ind)

    # Act
    orders = strat.on_new_bar(59, ind, _Account([]))

    # Assert
    assert len(orders) == 1 and orders[0].side == "buy"


class _CountingIndicators:
    """on_new_bar へ渡す indicators（ポートの注入点）を包み、get の発行を数える Test Spy。"""

    def __init__(self, inner):
        self._inner = inner
        self.gets_by_bar: dict[int, int] = {}
        self.bar = -1

    def get(self, name):
        self.gets_by_bar[self.bar] = self.gets_by_bar.get(self.bar, 0) + 1
        return self._inner.get(name)


def _reads_per_bar(n):
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    spy = _CountingIndicators(_long_buy_registry(n))
    strat = ProFitBand()
    strat.on_init(_CONFIG, spy)
    for i in range(n):
        spy.bar = i
        strat.on_new_bar(i, spy, _Account([]))
    return spy.gets_by_bar


def test_warmup_bars_read_no_indicator_and_reads_per_bar_do_not_grow_with_length():
    # Arrange / Act: 系列長 70 / 140 の 2 点で全足を評価する。
    short, long_ = _reads_per_bar(70), _reads_per_bar(140)

    # Assert: warmup 内（bar_index<59）の足は判定しない → 指標の読み − 0 = 0（出力に使わない読みを発行しない）。
    for reads in (short, long_):
        assert sum(c for b, c in reads.items() if b < 59) == 0
        assert reads.get(59, 0) > 0  # 対照: warmup 後は読む（spy が空振りしていない）
    # Assert: warmup 後の 1 足あたりの読みは系列長で増えない（回数は焼き込まない）。
    assert max(long_.values()) == max(short.values())


# --- 決済方針: on_position_check は固定 SL/TP のみ → 常に hold -----------------

def test_on_position_check_always_holds():
    # Arrange: PRO!fit_Band は反転決済なし（固定 SL/TP のみ）→ "hold"
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _registry(
        ema=[1.0, 1.1, 1.2],
        adx=[10.0, 20.0, 25.0],
        plus_di=[10.0, 20.0, 30.0],
        minus_di=[20.0, 15.0, 10.0],
        close=[1.2000, 1.2010, 1.2020],
    )
    strat.on_init(_CONFIG, ind)

    # Act
    decision = strat.on_position_check(None, _NOW, ind)

    # Assert
    assert decision == "hold"


# --- 原典オラクル（一次情報 = PRO!fit_Band.mq5）------------------------------
# 原典 input 既定値（PRO!fit_Band.mq5 L19-31）。production を import せずスカラー再計算する.
_ORACLE_CFG = {
    "stop_loss": 30,       # input int StopLoss=30
    "take_profit": 100,    # input int TakeProfit=100
    "adx_min": 22.0,       # input double Adx_Min=22.0
    "lot": 0.1,            # input double Lot=0.1
    "point": 0.0001,       # _Point（決定論固定）
}


def _oracle_order(ema, adx, plus_di, minus_di, close, bar_index, held_sides, digits):
    """原典 PRO!fit_Band.mq5 の OnTick 判定を production 非依存で再計算する独立オラクル.

    参照規約（PROCESS §0.3 / 原典 ArraySetAsSeries(true)）:
        MQL [0]=現足=iloc[bar_index], [1]=iloc[bar_index-1], [2]=iloc[bar_index-2].
        p_close = mrate[1].close = close[bar_index-1]（1 本前の確定終値・原典 L361）.
    桁補正（原典 OnInit L85-92）: _Digits∈{3,5} → STP=StopLoss*10, TKP=TakeProfit*10.
    SL/TP（原典 L413-415 / L507-509）:
        買い Ask=close[bar_index]: sl=Ask−STP*_Point, tp=Ask+TKP*_Point.
        売り Bid=close[bar_index]: sl=Bid+STP*_Point, tp=Bid−TKP*_Point.
    重複抑止（原典 L397-405 / L491-499）: 同方向保有のみ抑止（反対方向は抑止しない）.

    Returns:
        ``(side, sl, tp, volume)`` または発注なしの ``None``.
    """
    if bar_index + 1 < 60:         # 原典 Bars<60 ゲート（現足含む総本数 = bar_index+1・原典 L129）
        return None
    cfg = _ORACLE_CFG
    ma0, ma1, ma2 = ema[bar_index], ema[bar_index - 1], ema[bar_index - 2]
    adx0 = adx[bar_index]
    pdi0, mdi0 = plus_di[bar_index], minus_di[bar_index]
    p_close = close[bar_index - 1]
    price = close[bar_index]
    mult = 10 if digits in (3, 5) else 1
    stp = cfg["stop_loss"] * mult
    tkp = cfg["take_profit"] * mult
    pt = cfg["point"]
    # 買い: Buy_Condition_1..4 全 AND・厳密不等号（原典 L375-381・L387-391）
    if ma0 > ma1 and ma1 > ma2 and p_close > ma1 and adx0 > cfg["adx_min"] and pdi0 > mdi0:
        if "buy" in held_sides:
            return None
        return ("buy", price - stp * pt, price + tkp * pt, cfg["lot"])
    # 売り: Sell_Condition_1..4 全 AND・厳密不等号（原典 L469-475・L481-485）
    if ma0 < ma1 and ma1 < ma2 and p_close < ma1 and adx0 > cfg["adx_min"] and pdi0 < mdi0:
        if "sell" in held_sides:
            return None
        return ("sell", price + stp * pt, price - tkp * pt, cfg["lot"])
    return None


# 合成系列バッテリ: 買い成立・売り成立・各不成立（ADX/DI/EMA非単調/p_close位置）・
# 同方向保有抑止・反対保有では発注可。digits 5/3（×10）と 4（×1）で桁補正も網羅.
_ORACLE_CASES = [
    # (id, ema, adx, plus_di, minus_di, close, held)
    ("buy_signal", [1.0, 1.1, 1.2], [10.0, 20.0, 25.0], [10.0, 20.0, 30.0],
     [20.0, 15.0, 10.0], [1.2000, 1.2010, 1.2020], []),
    ("sell_signal", [1.2, 1.1, 1.0], [10.0, 20.0, 25.0], [20.0, 15.0, 10.0],
     [10.0, 20.0, 30.0], [1.3000, 1.0500, 1.2990], []),
    ("adx_at_threshold_no_order", [1.0, 1.1, 1.2], [10.0, 20.0, 22.0], [10.0, 20.0, 30.0],
     [20.0, 15.0, 10.0], [1.2000, 1.2010, 1.2020], []),
    ("di_reversed_no_order", [1.0, 1.1, 1.2], [10.0, 20.0, 25.0], [10.0, 20.0, 15.0],
     [5.0, 10.0, 20.0], [1.2000, 1.2010, 1.2020], []),
    ("ema_not_monotonic_no_order", [1.15, 1.1, 1.2], [10.0, 20.0, 25.0], [10.0, 20.0, 30.0],
     [20.0, 15.0, 10.0], [1.2000, 1.2010, 1.2020], []),
    ("prev_close_below_ema1_no_order", [1.0, 1.1, 1.2], [10.0, 20.0, 25.0], [10.0, 20.0, 30.0],
     [20.0, 15.0, 10.0], [1.2000, 1.0500, 1.2020], []),
    ("same_side_buy_blocked", [1.0, 1.1, 1.2], [10.0, 20.0, 25.0], [10.0, 20.0, 30.0],
     [20.0, 15.0, 10.0], [1.2000, 1.2010, 1.2020], ["buy"]),
    ("opposite_side_sell_allows_buy", [1.0, 1.1, 1.2], [10.0, 20.0, 25.0], [10.0, 20.0, 30.0],
     [20.0, 15.0, 10.0], [1.2000, 1.2010, 1.2020], ["sell"]),
]


@pytest.mark.parametrize("digits", [5, 3, 4])
@pytest.mark.parametrize(
    "case_id,ema,adx,plus_di,minus_di,close,held",
    _ORACLE_CASES,
    ids=[c[0] for c in _ORACLE_CASES],
)
def test_order_matches_mql5_source_oracle(case_id, ema, adx, plus_di, minus_di, close, held, digits):
    # Arrange: 原典 input 既定値（SL=30/TP=100/Adx_Min=22/Lot=0.1）を production config に写像.
    #          オラクルは production を import せず原典 .mq5 条件から独立に期待値を計算する.
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    cfg = dict(_CONFIG, digits=digits)  # _CONFIG: lot/SL/TP/adx_min/point は原典既定と一致
    strat = ProFitBand()
    ind = _registry(ema=ema, adx=adx, plus_di=plus_di, minus_di=minus_di, close=close)
    strat.on_init(cfg, ind)
    expected = _oracle_order(
        _padded(ema), _padded(adx), _padded(plus_di), _padded(minus_di), _padded(close),
        _NOW, set(held), digits,
    )

    # Act
    orders = strat.on_new_bar(_NOW, ind, _Account(held))

    # Assert: 発注有無・side/sl/tp/volume が原典オラクルと一致（SL/TP は厳密一致＝手計算固定）.
    if expected is None:
        assert orders == []
    else:
        assert len(orders) == 1
        o = orders[0]
        side, sl, tp, vol = expected
        assert (o.side, o.kind, o.price) == (side, "market", None)
        assert o.volume == vol
        assert o.sl == pytest.approx(sl, abs=1e-12)
        assert o.tp == pytest.approx(tp, abs=1e-12)


def test_oracle_buy_sltp_absolute_values_are_hand_computed_for_digits5():
    # Arrange: トートロジー回避の固定点。買い成立・digits=5・price=close[2]=1.2020.
    #          原典式 sl=Ask−STP*_Point / tp=Ask+TKP*_Point を手計算した絶対値で固定する.
    from simulator.adapter.strategy.pro_fit_band import ProFitBand

    strat = ProFitBand()
    ind = _registry(
        ema=[1.0, 1.1, 1.2],
        adx=[10.0, 20.0, 25.0],
        plus_di=[10.0, 20.0, 30.0],
        minus_di=[20.0, 15.0, 10.0],
        close=[1.2000, 1.2010, 1.2020],
    )
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(_NOW, ind, _Account([]))

    # Assert: 手計算（digits=5 → STP=300, TKP=1000, _Point=0.0001）.
    #   sl = 1.2020 − 300*0.0001 = 1.1720 ／ tp = 1.2020 + 1000*0.0001 = 1.3020.
    o = orders[0]
    assert o.side == "buy"
    assert o.sl == pytest.approx(1.1720, abs=1e-12)
    assert o.tp == pytest.approx(1.3020, abs=1e-12)
