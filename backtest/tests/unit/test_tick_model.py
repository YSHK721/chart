"""adapter/execution/tick_model.py の TickModel テスト（TickModelPort・PROCESS §0.2/§7-#1）。

TickModelPort.ticks_of(bar, prev_close) -> Iterable[Tick]   # Tick = (price, bid, ask, time)

実装（CLEAN_ARCH §6.3）:
    - OhlcExpandTickModel: 1 バーを O→H→L→C の 4 疑似ティックへ展開（決定論）。
    - OpenOnlyTickModel  : 始値のみ（1 ティック）。
    - EveryTickModel     : 実ティック列。OHLC のみの入力では O→H→L→C 近似へフォールバック
      （実ティック未供給時の決定論的近似。Dukascopy 実ティック供給は範囲外＝将来）。

最小骨格: spread=0 のとき bid=ask=price（実 spread は spread_model 接続時に拡張＝範囲外）。
"""
from __future__ import annotations

import numpy as np

from backtest.domain.bar import Bar
from backtest.usecase.ports import TickModelPort


def _bar():
    return Bar(time=0, open=1.0, high=1.5, low=0.8, close=1.2, volume=10.0, spread=0)


# --- OhlcExpandTickModel ----------------------------------------------------

def test_ohlc_expand_implements_tick_model_port():
    # Arrange / Act
    from backtest.adapter.execution.tick_model import OhlcExpandTickModel

    # Assert: LSP
    assert isinstance(OhlcExpandTickModel(), TickModelPort)


def test_ohlc_expand_yields_open_high_low_close_in_order():
    # Arrange: O→H→L→C の 4 疑似ティック（PROCESS §7-#5 ohlc_order）
    from backtest.adapter.execution.tick_model import OhlcExpandTickModel

    bar = _bar()

    # Act
    ticks = list(OhlcExpandTickModel().ticks_of(bar, prev_close=1.1))

    # Assert: price 列が O→H→L→C
    prices = [t[0] for t in ticks]
    assert prices == [1.0, 1.5, 0.8, 1.2]


def test_ohlc_expand_tick_has_price_bid_ask_time_shape():
    # Arrange: Tick = (price, bid, ask, time)。spread=0 のとき bid=ask=price
    from backtest.adapter.execution.tick_model import OhlcExpandTickModel

    bar = _bar()

    # Act
    first = list(OhlcExpandTickModel().ticks_of(bar, prev_close=1.1))[0]

    # Assert
    assert len(first) == 4
    price, bid, ask, time = first
    assert price == 1.0 and bid == 1.0 and ask == 1.0
    assert time == bar.time


# --- OhlcExpandTickModel order="auto"（実 MT5 OHLC 順序則・2603-01 で実証） ----

def _b(o, h, l, c, vol=10.0, spread=0):
    return Bar(time=0, open=o, high=h, low=l, close=c, volume=vol, spread=spread)


def _prices(model, bar, prev_close=0.0):
    return [t[0] for t in model.ticks_of(bar, prev_close=prev_close)]


def test_auto_bullish_bar_visits_low_first():
    # 強気足（close>open）は安値先 O→L→H→C（実体方向と逆の極値へ先に振れる）
    from backtest.adapter.execution.tick_model import OhlcExpandTickModel

    m = OhlcExpandTickModel(order="auto")
    assert _prices(m, _b(1.0, 1.5, 0.8, 1.2)) == [1.0, 0.8, 1.5, 1.2]


def test_auto_bearish_bar_visits_high_first():
    # 弱気足（close<open）は高値先 O→H→L→C
    from backtest.adapter.execution.tick_model import OhlcExpandTickModel

    m = OhlcExpandTickModel(order="auto")
    assert _prices(m, _b(1.2, 1.5, 0.8, 1.0)) == [1.2, 1.5, 0.8, 1.0]


def test_auto_doji_follows_previous_bar_direction():
    # ドジ足（close==open）は直前足のモメンタムを継続。前足陽→高値先 / 前足陰→安値先。
    from backtest.adapter.execution.tick_model import OhlcExpandTickModel

    m = OhlcExpandTickModel(order="auto")
    # 前足: 陽線（close>open）
    m.ticks_of(_b(1.0, 1.1, 1.0, 1.1), prev_close=0.0)
    assert _prices(m, _b(1.2, 1.5, 0.8, 1.2)) == [1.2, 1.5, 0.8, 1.2]  # 高値先
    # 前足: 陰線（close<open）
    m2 = OhlcExpandTickModel(order="auto")
    m2.ticks_of(_b(1.1, 1.1, 1.0, 1.0), prev_close=0.0)
    assert _prices(m2, _b(1.2, 1.5, 0.8, 1.2)) == [1.2, 0.8, 1.5, 1.2]  # 安値先


def test_auto_thin_bar_dedups_adjacent_equal_ticks():
    # tickvol<4 は隣接等値を集約（実ティック 4 本未満ゆえ）。
    from backtest.adapter.execution.tick_model import OhlcExpandTickModel

    m = OhlcExpandTickModel(order="auto")
    # 強気 O=L, H=C, tickvol=2 → O→L→H→C=[1.0,1.0,1.2,1.2] を集約して [1.0,1.2]
    assert _prices(m, _b(1.0, 1.2, 1.0, 1.2, vol=2.0)) == [1.0, 1.2]


def test_auto_thick_bar_keeps_duplicate_ticks():
    # tickvol>=4 は等値の隣接も別ティックとして 4 件保持（同足で約定後の決済を可能にする）。
    from backtest.adapter.execution.tick_model import OhlcExpandTickModel

    m = OhlcExpandTickModel(order="auto")
    assert _prices(m, _b(1.0, 1.2, 1.0, 1.2, vol=10.0)) == [1.0, 1.0, 1.2, 1.2]


def test_default_order_unchanged_ohlc():
    # 既定 order="ohlc" は従来どおり O→H→L→C（4 件・dedup なし）で後方互換。
    from backtest.adapter.execution.tick_model import OhlcExpandTickModel

    m = OhlcExpandTickModel()
    assert _prices(m, _b(1.0, 1.2, 1.0, 1.2, vol=2.0)) == [1.0, 1.2, 1.0, 1.2]


# --- OpenOnlyTickModel ------------------------------------------------------

def test_open_only_yields_single_tick_at_open():
    # Arrange: 始値のみ（1 ティック）
    from backtest.adapter.execution.tick_model import OpenOnlyTickModel

    bar = _bar()

    # Act
    ticks = list(OpenOnlyTickModel().ticks_of(bar, prev_close=1.1))

    # Assert
    assert len(ticks) == 1
    assert ticks[0][0] == 1.0  # price == open


# --- EveryTickModel（OHLC 入力フォールバック）------------------------------

def test_every_tick_falls_back_to_ohlc_expand_without_real_ticks():
    # Arrange: 実ティック未供給時は O→H→L→C 近似（決定論）
    from backtest.adapter.execution.tick_model import EveryTickModel

    bar = _bar()

    # Act
    prices = [t[0] for t in EveryTickModel().ticks_of(bar, prev_close=1.1)]

    # Assert
    assert prices == [1.0, 1.5, 0.8, 1.2]


# --- RealTickModel（every-tick #4・実ティック frame からバー区間をスライス）-----------
# 保持する実ティック frame（canonical: timestamp/bid/ask/last/volume）から、当該バー区間
# [bar.time, bar.time+足長) の実ティックを Tick=(price,bid,ask,time) へ整形して返す。
# price は canonical last（mid）採用。pandas は adapter 内に隔離（usecase へ漏らさない）。
# 区間にティック0件なら空 Iterable。prev_close は契約上受けるが整形では未使用。
# 足長は M1=60s 前提（推奨案: bar.time 型に応じ datetime64 は timedelta64(60,'s')、
# epoch int は +60 を加算して区間終端を求める）。

def _tick_frame():
    """3 本のバー区間にまたがる構成済 frame（M1・datetime64）。

    bar 00:00 区間 [00:00, 01:00) に 2 ティック、01:00 区間に 1 ティック、
    02:00 区間（境界外）に 1 ティックを配置し、区間スライスの決定論を検証する。
    """
    import pandas as pd

    return pd.DataFrame(
        {
            "timestamp": [
                np.datetime64("2024-01-01T00:00:10"),
                np.datetime64("2024-01-01T00:00:30"),
                np.datetime64("2024-01-01T00:01:05"),
                np.datetime64("2024-01-01T00:02:00"),
            ],
            "bid": [1.10, 1.11, 1.20, 1.30],
            "ask": [1.12, 1.13, 1.22, 1.32],
            "last": [1.11, 1.12, 1.21, 1.31],
            "volume": [1.0, 2.0, 3.0, 4.0],
        }
    )


def _bar_at(minute: int):
    t = np.datetime64("2024-01-01T00:00:00") + np.timedelta64(minute, "m")
    return Bar(time=t, open=1.1, high=1.3, low=1.0, close=1.2, volume=10.0, spread=0)


def test_real_tick_implements_tick_model_port():
    # LSP: TickModelPort を実装する。
    from backtest.adapter.execution.tick_model import RealTickModel

    assert isinstance(RealTickModel(_tick_frame()), TickModelPort)


def test_real_tick_slices_only_ticks_within_bar_interval():
    # [00:00, 01:00) の 2 ティックのみを返す（01:05 と 02:00 は区間外）。
    from backtest.adapter.execution.tick_model import RealTickModel

    ticks = list(RealTickModel(_tick_frame()).ticks_of(_bar_at(0), prev_close=1.0))

    # price は canonical last 採用・区間内 2 件・決定論順（timestamp 昇順）。
    prices = [t[0] for t in ticks]
    assert prices == [1.11, 1.12]


def test_real_tick_maps_price_bid_ask_time_shape():
    # Tick = (price=last, bid, ask, time=timestamp) の写像が決定論。
    from backtest.adapter.execution.tick_model import RealTickModel

    first = list(RealTickModel(_tick_frame()).ticks_of(_bar_at(0), prev_close=1.0))[0]

    assert len(first) == 4
    price, bid, ask, time = first
    assert price == 1.11   # last
    assert bid == 1.10
    assert ask == 1.12
    assert time == np.datetime64("2024-01-01T00:00:10")


def test_real_tick_next_bar_interval_slices_single_tick():
    # [01:00, 02:00) は 01:05 の 1 ティックのみ（区間端は半開・02:00 は含まない）。
    from backtest.adapter.execution.tick_model import RealTickModel

    ticks = list(RealTickModel(_tick_frame()).ticks_of(_bar_at(1), prev_close=1.0))
    prices = [t[0] for t in ticks]
    assert prices == [1.21]


def test_real_tick_empty_interval_yields_empty():
    # ティック 0 件のバー区間（[03:00, 04:00) には frame 上ティックなし）は空 Iterable。
    from backtest.adapter.execution.tick_model import RealTickModel

    ticks = list(RealTickModel(_tick_frame()).ticks_of(_bar_at(3), prev_close=1.0))
    assert ticks == []


def test_real_tick_time_is_numpy_datetime64_not_pandas_timestamp():
    # pandas を adapter 内に隔離する: Tick の time は domain の numpy.datetime64 であり、
    # pandas.Timestamp を usecase/domain へ漏らさない（Bar.time 契約: pd.Timestamp 禁止）。
    import pandas as pd
    from backtest.adapter.execution.tick_model import RealTickModel

    first = list(RealTickModel(_tick_frame()).ticks_of(_bar_at(0), prev_close=1.0))[0]
    time = first[3]

    assert isinstance(time, np.datetime64)
    assert not isinstance(time, pd.Timestamp)


# --- RealTickModel 順序保証（レビュー 🟡-1・順序ハザード回帰）-------------------
# every-tick は順序依存（最初tick=約定価格・tick列順=SL/TP/stop-out発火順）。docstring が
# 「timestamp 昇順へ整形」と謳う以上、frame 行順が非ソートでも ticks_of は昇順を返すこと
# を不変条件として固定する（実 Dukascopy が偶然昇順だった＝未保証 を禁止する回帰テスト）。


def _unsorted_tick_frame():
    """同一バー区間 [00:00, 01:00) に timestamp 降順で並べた非ソート frame。

    frame 行順は 00:00:50 → 00:00:30 → 00:00:10 の降順。ソートしなければ
    ticks_of は行順のまま降順を返す（= 期待昇順と不一致で落ちる）。
    """
    import pandas as pd

    return pd.DataFrame(
        {
            "timestamp": [
                np.datetime64("2024-01-01T00:00:50"),
                np.datetime64("2024-01-01T00:00:30"),
                np.datetime64("2024-01-01T00:00:10"),
            ],
            "bid": [1.30, 1.20, 1.10],
            "ask": [1.32, 1.22, 1.12],
            "last": [1.33, 1.22, 1.11],
            "volume": [3.0, 2.0, 1.0],
        }
    )


def test_real_tick_returns_ticks_in_timestamp_ascending_order_for_unsorted_frame():
    # Arrange: 非ソート（降順）frame を与える。
    from backtest.adapter.execution.tick_model import RealTickModel

    # Act: 同一バー区間の全ティックを取得する。
    ticks = list(
        RealTickModel(_unsorted_tick_frame()).ticks_of(_bar_at(0), prev_close=1.0)
    )

    # Assert: time 列が timestamp 昇順（行順の降順でなく時刻順に整形されている）。
    times = [t[3] for t in ticks]
    assert times == [
        np.datetime64("2024-01-01T00:00:10"),
        np.datetime64("2024-01-01T00:00:30"),
        np.datetime64("2024-01-01T00:00:50"),
    ]
    # price も time に追従して昇順整形される（行順の last=[1.33,1.22,1.11] でない）。
    prices = [t[0] for t in ticks]
    assert prices == [1.11, 1.22, 1.33]
