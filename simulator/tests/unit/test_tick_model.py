"""adapter/execution/tick_model.py の TickModel テスト（TickModelPort・PROCESS §0.2/§7-#1）。

TickModelPort.ticks_of(bar, prev_close) -> Iterable[Tick]   # Tick = (price, bid, ask, time)

実装（CLEAN_ARCH §6.3）:
    - OhlcExpandTickModel: 1 バーを O→H→L→C の 4 疑似ティックへ展開（決定論）。
    - OpenOnlyTickModel  : 始値のみ（1 ティック）。
    - EveryTickModel     : OHLC のみの入力での O→H→L→C 決定論的近似（実ティックは
      供給しない。frame を持たないため常にフォールバックする）。
    - RealTickModel      : tick-store の実ティック frame をバー区間 [bar.time, +足長) へ
      切り出す（区間算定の時刻表現非依存は本ファイル末尾で固定・ISSUE-403）。

最小骨格: spread=0 のとき bid=ask=price（実 spread は spread_model 接続時に拡張＝範囲外）。
"""
from __future__ import annotations

import numpy as np

from simulator.domain.bar import Bar
from simulator.usecase.ports import TickModelPort


def _bar():
    return Bar(time=0, open=1.0, high=1.5, low=0.8, close=1.2, volume=10.0, spread=0)


# --- OhlcExpandTickModel ----------------------------------------------------

def test_ohlc_expand_implements_tick_model_port():
    # Arrange / Act
    from simulator.adapter.execution.tick_model import OhlcExpandTickModel

    # Assert: LSP
    assert isinstance(OhlcExpandTickModel(), TickModelPort)


def test_ohlc_expand_yields_open_high_low_close_in_order():
    # Arrange: O→H→L→C の 4 疑似ティック（PROCESS §7-#5 ohlc_order）
    from simulator.adapter.execution.tick_model import OhlcExpandTickModel

    bar = _bar()

    # Act
    ticks = list(OhlcExpandTickModel().ticks_of(bar, prev_close=1.1))

    # Assert: price 列が O→H→L→C
    prices = [t[0] for t in ticks]
    assert prices == [1.0, 1.5, 0.8, 1.2]


def test_ohlc_expand_tick_has_price_bid_ask_time_shape():
    # Arrange: Tick = (price, bid, ask, time)。spread=0 のとき bid=ask=price
    from simulator.adapter.execution.tick_model import OhlcExpandTickModel

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
    from simulator.adapter.execution.tick_model import OhlcExpandTickModel

    m = OhlcExpandTickModel(order="auto")
    assert _prices(m, _b(1.0, 1.5, 0.8, 1.2)) == [1.0, 0.8, 1.5, 1.2]


def test_auto_bearish_bar_visits_high_first():
    # 弱気足（close<open）は高値先 O→H→L→C
    from simulator.adapter.execution.tick_model import OhlcExpandTickModel

    m = OhlcExpandTickModel(order="auto")
    assert _prices(m, _b(1.2, 1.5, 0.8, 1.0)) == [1.2, 1.5, 0.8, 1.0]


def test_auto_doji_follows_previous_bar_direction():
    # ドジ足（close==open）は直前足のモメンタムを継続。前足陽→高値先 / 前足陰→安値先。
    from simulator.adapter.execution.tick_model import OhlcExpandTickModel

    m = OhlcExpandTickModel(order="auto")
    # 前足: 陽線（close>open）
    m.ticks_of(_b(1.0, 1.1, 1.0, 1.1), prev_close=0.0)
    assert _prices(m, _b(1.2, 1.5, 0.8, 1.2)) == [1.2, 1.5, 0.8, 1.2]  # 高値先
    # 前足: 陰線（close<open）
    m2 = OhlcExpandTickModel(order="auto")
    m2.ticks_of(_b(1.1, 1.1, 1.0, 1.0), prev_close=0.0)
    assert _prices(m2, _b(1.2, 1.5, 0.8, 1.2)) == [1.2, 0.8, 1.5, 1.2]  # 安値先


def test_auto_doji_chain_carries_last_nondoji_direction():
    # ドジ連鎖（直前足もドジ）でも、最後に方向を持った非ドジ足のモメンタムを継続する
    #   （2604-02・ISSUE-024）。ドジ足は prev を上書きしないため連鎖を跨いで方向が保たれる。
    from simulator.adapter.execution.tick_model import OhlcExpandTickModel

    # 非ドジ陽線 → ドジ1 → ドジ2。ドジ1/2 とも高値先（陽線モメンタム継続）。
    m = OhlcExpandTickModel(order="auto")
    m.ticks_of(_b(1.0, 1.1, 1.0, 1.1), prev_close=0.0)  # 陽線（方向確定）
    assert _prices(m, _b(1.2, 1.5, 0.8, 1.2)) == [1.2, 1.5, 0.8, 1.2]  # ドジ1: 高値先
    assert _prices(m, _b(1.3, 1.6, 0.9, 1.3)) == [1.3, 1.6, 0.9, 1.3]  # ドジ2: 連鎖継続=高値先
    # 非ドジ陰線 → ドジ連鎖は安値先を継続。
    m2 = OhlcExpandTickModel(order="auto")
    m2.ticks_of(_b(1.1, 1.1, 1.0, 1.0), prev_close=0.0)  # 陰線
    assert _prices(m2, _b(1.2, 1.5, 0.8, 1.2)) == [1.2, 0.8, 1.5, 1.2]  # ドジ1: 安値先
    assert _prices(m2, _b(1.3, 1.6, 0.9, 1.3)) == [1.3, 0.9, 1.6, 1.3]  # ドジ2: 連鎖継続=安値先


def test_auto_thin_bar_dedups_adjacent_equal_ticks():
    # tickvol<4 は隣接等値を集約（実ティック 4 本未満ゆえ）。
    from simulator.adapter.execution.tick_model import OhlcExpandTickModel

    m = OhlcExpandTickModel(order="auto")
    # 強気 O=L, H=C, tickvol=2 → O→L→H→C=[1.0,1.0,1.2,1.2] を集約して [1.0,1.2]
    assert _prices(m, _b(1.0, 1.2, 1.0, 1.2, vol=2.0)) == [1.0, 1.2]


def test_auto_thick_bar_keeps_duplicate_ticks():
    # tickvol>=4 は等値の隣接も別ティックとして 4 件保持（同足で約定後の決済を可能にする）。
    from simulator.adapter.execution.tick_model import OhlcExpandTickModel

    m = OhlcExpandTickModel(order="auto")
    assert _prices(m, _b(1.0, 1.2, 1.0, 1.2, vol=10.0)) == [1.0, 1.0, 1.2, 1.2]


def test_default_order_unchanged_ohlc():
    # 既定 order="ohlc" は従来どおり O→H→L→C（4 件・dedup なし）で後方互換。
    from simulator.adapter.execution.tick_model import OhlcExpandTickModel

    m = OhlcExpandTickModel()
    assert _prices(m, _b(1.0, 1.2, 1.0, 1.2, vol=2.0)) == [1.0, 1.2, 1.0, 1.2]


# --- OpenOnlyTickModel ------------------------------------------------------

def test_open_only_yields_single_tick_at_open():
    # Arrange: 始値のみ（1 ティック）
    from simulator.adapter.execution.tick_model import OpenOnlyTickModel

    bar = _bar()

    # Act
    ticks = list(OpenOnlyTickModel().ticks_of(bar, prev_close=1.1))

    # Assert
    assert len(ticks) == 1
    assert ticks[0][0] == 1.0  # price == open


# --- EveryTickModel（OHLC 入力フォールバック）------------------------------

def test_every_tick_falls_back_to_ohlc_expand_without_real_ticks():
    # Arrange: 実ティック未供給時は O→H→L→C 近似（決定論）
    from simulator.adapter.execution.tick_model import EveryTickModel

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
    from simulator.adapter.execution.tick_model import RealTickModel

    assert isinstance(RealTickModel(_tick_frame()), TickModelPort)


def test_real_tick_slices_only_ticks_within_bar_interval():
    # [00:00, 01:00) の 2 ティックのみを返す（01:05 と 02:00 は区間外）。
    from simulator.adapter.execution.tick_model import RealTickModel

    ticks = list(RealTickModel(_tick_frame()).ticks_of(_bar_at(0), prev_close=1.0))

    # price は canonical last 採用・区間内 2 件・決定論順（timestamp 昇順）。
    prices = [t[0] for t in ticks]
    assert prices == [1.11, 1.12]


def test_real_tick_maps_price_bid_ask_time_shape():
    # Tick = (price=last, bid, ask, time=timestamp) の写像が決定論。
    from simulator.adapter.execution.tick_model import RealTickModel

    first = list(RealTickModel(_tick_frame()).ticks_of(_bar_at(0), prev_close=1.0))[0]

    assert len(first) == 4
    price, bid, ask, time = first
    assert price == 1.11   # last
    assert bid == 1.10
    assert ask == 1.12
    assert time == np.datetime64("2024-01-01T00:00:10")


def test_real_tick_next_bar_interval_slices_single_tick():
    # [01:00, 02:00) は 01:05 の 1 ティックのみ（区間端は半開・02:00 は含まない）。
    from simulator.adapter.execution.tick_model import RealTickModel

    ticks = list(RealTickModel(_tick_frame()).ticks_of(_bar_at(1), prev_close=1.0))
    prices = [t[0] for t in ticks]
    assert prices == [1.21]


def test_real_tick_empty_interval_yields_empty():
    # ティック 0 件のバー区間（[03:00, 04:00) には frame 上ティックなし）は空 Iterable。
    from simulator.adapter.execution.tick_model import RealTickModel

    ticks = list(RealTickModel(_tick_frame()).ticks_of(_bar_at(3), prev_close=1.0))
    assert ticks == []


def test_real_tick_time_is_numpy_datetime64_not_pandas_timestamp():
    # pandas を adapter 内に隔離する: Tick の time は domain の numpy.datetime64 であり、
    # pandas.Timestamp を usecase/domain へ漏らさない（Bar.time 契約: pd.Timestamp 禁止）。
    import pandas as pd
    from simulator.adapter.execution.tick_model import RealTickModel

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
    from simulator.adapter.execution.tick_model import RealTickModel

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


# --- RealTickModel の時刻表現非依存性（ISSUE-403）-------------------------------
# `Bar.time` の契約は ``numpy.datetime64`` | epoch int であり、comma 形式 CSV 経路では
# pandas が返す実型＝``numpy.int64`` になる。区間スライスは「どの表現で書かれたバーか」に
# 依存してはならない。是正前は手書きディスパッチ（`_normalize_bar_time`）が
# ``isinstance(np.int64, int)`` = **False**（実測・numpy 2.4.6）で epoch 枝を外し、
# ``np.datetime64(np.int64(...))`` の ``ValueError`` で落ちていた（ISSUE-403）。


def _epoch_bar_at(minute: int):
    """`_bar_at` と同一時刻を epoch int（Python int）で表したバー。"""
    return Bar(
        time=1_704_067_200 + 60 * minute,
        open=1.1, high=1.3, low=1.0, close=1.2, volume=10.0, spread=0,
    )


def _np_int64_bar_at(minute: int):
    """`_bar_at` と同一時刻を ``numpy.int64`` で表したバー（comma 形式 CSV の実型）。"""
    return Bar(
        time=np.int64(1_704_067_200 + 60 * minute),
        open=1.1, high=1.3, low=1.0, close=1.2, volume=10.0, spread=0,
    )


def test_real_tick_numpy_int64_bar_time_slices_the_same_interval_as_datetime64():
    # Arrange: 同一時刻を numpy.datetime64 と numpy.int64 で表した 2 本のバー。
    from simulator.adapter.execution.tick_model import RealTickModel

    model = RealTickModel(_tick_frame())
    # Act
    via_dt64 = list(model.ticks_of(_bar_at(0), prev_close=1.0))
    via_int64 = list(model.ticks_of(_np_int64_bar_at(0), prev_close=1.0))
    # Assert: 表現が違っても同一ティック集合。
    assert via_int64 == via_dt64


def test_real_tick_python_int_bar_time_slices_the_same_interval_as_datetime64():
    # Arrange: 同一時刻を numpy.datetime64 と Python int（epoch 秒）で表した 2 本のバー。
    from simulator.adapter.execution.tick_model import RealTickModel

    model = RealTickModel(_tick_frame())
    # Act
    via_dt64 = list(model.ticks_of(_bar_at(0), prev_close=1.0))
    via_int = list(model.ticks_of(_epoch_bar_at(0), prev_close=1.0))
    # Assert
    assert via_int == via_dt64


def test_real_tick_epoch_bar_time_honours_the_half_open_interval():
    # Arrange: epoch int 表現でも区間は半開 [bar.time, bar.time+60s)。
    from simulator.adapter.execution.tick_model import RealTickModel

    model = RealTickModel(_tick_frame())
    # Act: 01:00 の足は 01:05 の 1 件のみ（02:00 は次足＝含まない）。
    prices = [t[0] for t in model.ticks_of(_np_int64_bar_at(1), prev_close=1.0)]
    # Assert
    assert prices == [1.21]


def test_real_tick_includes_the_tick_exactly_at_the_bar_start_boundary():
    # 開始境界ちょうど（timestamp == bar.time）のティックは区間に**含まれる**
    #   （半開 [start, end) の左端。ISSUE-413-7: 従来の標本は 00:00:10 以降のみで、
    #   左端ちょうどの包含が未固定だった）。終端ちょうど（次バー始端）は含まれない。
    import pandas as pd
    from simulator.adapter.execution.tick_model import RealTickModel

    frame = pd.DataFrame(
        {
            "timestamp": [
                np.datetime64("2024-01-01T00:01:00"),  # bar1 の始端ちょうど
                np.datetime64("2024-01-01T00:01:59"),  # bar1 の終端 1 秒前
                np.datetime64("2024-01-01T00:02:00"),  # bar2 の始端＝bar1 に含まない
            ],
            "bid": [1.10, 1.20, 1.30],
            "ask": [1.12, 1.22, 1.32],
            "last": [1.11, 1.21, 1.31],
            "volume": [1.0, 2.0, 3.0],
        }
    )

    times = [t[3] for t in RealTickModel(frame).ticks_of(_bar_at(1), prev_close=1.0)]

    assert times == [
        np.datetime64("2024-01-01T00:01:00"),
        np.datetime64("2024-01-01T00:01:59"),
    ]


# --- 受理入力の狭まりの固定（ISSUE-403 互換性影響・ISSUE-413-1）-----------------
# ISO 文字列の `time` は元より `Bar.time` 契約違反であり、ISSUE-403 の是正で real_ticks
# 経路は **翻訳済み `ConfigError`**（終了コード表で exit 2）として拒否するようになった。
# 是正前の「翻訳されない ValueError が漏れる」挙動へ戻る退行を禁じる。


def test_real_tick_iso_string_bar_time_raises_a_translated_config_error():
    # Arrange: `Bar` は構築時契約検査（ISSUE-411）で str を拒否するため、tick_model の
    #   正規化点（`epoch_seconds`）そのものを固定するにはバー代替物で time=ISO 文字列を渡す。
    from types import SimpleNamespace

    import pytest

    from simulator.adapter.execution.tick_model import RealTickModel
    from simulator.domain.exceptions import ConfigError

    bar = SimpleNamespace(
        time="2024-01-01T00:00:00",
        open=1.1, high=1.3, low=1.0, close=1.2, volume=10.0, spread=0,
    )

    # Act / Assert: 翻訳済み ConfigError（生 ValueError ではない）。
    with pytest.raises(ConfigError):
        RealTickModel(_tick_frame()).ticks_of(bar, prev_close=1.0)


def test_bar_construction_rejects_iso_string_time_with_config_error():
    # comma 形式 CSV の ISO 文字列 `time` は Bar 構築段（契約検査・ISSUE-411）で既に
    #   ConfigError になる。real_ticks 経路の拒否が「どの段でも翻訳済み」であることの固定。
    import pytest

    from simulator.domain.exceptions import ConfigError

    with pytest.raises(ConfigError):
        Bar(
            time="2024-01-01T00:00:00",
            open=1.1, high=1.3, low=1.0, close=1.2, volume=10.0, spread=0,
        )


# --- RealTickModel の区間判定 ≡ HalfOpenEpochWindow.contains（ISSUE-413-2 ゲート）----
# tick_model の per-bar 判定は `HalfOpenEpochWindow.contains` と同一規則の等価実装である
# （実装は速度のためベクトル化/区間切り出しでよいが、選ぶ集合は contains が定義する）。
# 期待値をリテラルで書かず **contains そのものから導出**することで、半開規則が変更された
# とき（ISSUE-407/408 の系統）に実装との乖離を機械的に検出する。


def _boundary_epochs(start: int, bar_seconds: int) -> "list[int]":
    """窓の両端 ±1 秒・端ちょうど・中央を網羅する epoch 列（昇順）。"""
    end = start + bar_seconds
    return [start - 1, start, start + 1, start + bar_seconds // 2, end - 1, end, end + 1]


def test_real_tick_selection_is_defined_by_the_contains_predicate():
    # Arrange: 境界網羅の epoch 列を持つ frame（期待値は contains から導出する）。
    import pandas as pd
    from datawindow.half_open import HalfOpenEpochWindow
    from marketdata.tf_ledger import TF_BAR_SEC
    from simulator.adapter.execution.tick_model import RealTickModel
    from simulator.domain.bar_time import epoch_seconds

    bar = _np_int64_bar_at(1)
    start = epoch_seconds(bar.time)
    bar_seconds = TF_BAR_SEC["1m"]
    epochs = _boundary_epochs(start, bar_seconds)
    frame = pd.DataFrame(
        {
            "timestamp": [np.datetime64(e, "s") for e in epochs],
            "bid": [float(i) for i in range(len(epochs))],
            "ask": [float(i) + 0.5 for i in range(len(epochs))],
            "last": [float(i) + 0.25 for i in range(len(epochs))],
            "volume": [1.0] * len(epochs),
        }
    )
    window = HalfOpenEpochWindow(start, start + bar_seconds)
    expected = [e for e in epochs if window.contains(e)]
    # 自明合格の防止: 窓外の標本が両側に実在すること（全含み/全外れで通る当たりを塞ぐ）。
    assert expected and len(expected) < len(epochs)

    # Act
    ticks = list(RealTickModel(frame).ticks_of(bar, prev_close=1.0))

    # Assert: 選ばれる集合（と順序）は contains の定義と一致する。
    got = [int(t[3].astype("datetime64[s]").astype("int64")) for t in ticks]
    assert got == expected


def test_real_tick_selection_matches_contains_for_every_bar_offset():
    # 同じ frame を隣接バー（前後 1 本）でも判定し、窓の位置に依らず contains と一致する
    #   ことを固定する（境界規則の乖離は隣接バーへの取りこぼし/二重取りとして現れる）。
    import pandas as pd
    from datawindow.half_open import HalfOpenEpochWindow
    from marketdata.tf_ledger import TF_BAR_SEC
    from simulator.adapter.execution.tick_model import RealTickModel
    from simulator.domain.bar_time import epoch_seconds

    bar_seconds = TF_BAR_SEC["1m"]
    anchor = epoch_seconds(_np_int64_bar_at(1).time)
    epochs = _boundary_epochs(anchor, bar_seconds)
    frame = pd.DataFrame(
        {
            "timestamp": [np.datetime64(e, "s") for e in epochs],
            "bid": [1.0] * len(epochs),
            "ask": [1.0] * len(epochs),
            "last": [1.0] * len(epochs),
            "volume": [1.0] * len(epochs),
        }
    )
    model = RealTickModel(frame)

    for minute in (0, 1, 2):
        bar = _np_int64_bar_at(minute)
        start = epoch_seconds(bar.time)
        window = HalfOpenEpochWindow(start, start + bar_seconds)
        expected = [e for e in epochs if window.contains(e)]

        got = [
            int(t[3].astype("datetime64[s]").astype("int64"))
            for t in model.ticks_of(bar, prev_close=1.0)
        ]
        assert got == expected, f"minute={minute}"


# --- RealTickModel.ticks_of の計算量（ISSUE-413-4・計算量テスト規約 2026-08-28）--------
# ticks_of は 1 run につきバー本数回（実データ 28097 回）呼ばれる。per-bar の仕事量が
# frame **全長**に比例する実装（全行掃引の mask）は、出力が正しいまま O(全行×バー数) を
# 浪費する——状態検証では原理的に落ちない欠陥なので、掃引の不在を Spy で機械的に固定する。
# 回数そのものは期待値に焼き込まない（「N 回」の固定は浪費の仕様化）。固定するのは
# 「入力（区間外の行数）を増やしても per-bar の掃引量が増えない」というオーダーの表明と、
# 「発行した行 − 出力に使った行 = 0」（作ってから捨てる行がない）の 2 点である。


class _SweepCountingArray(np.ndarray):
    """要素ごと ufunc 演算（全行掃引）で触れた要素数を数える epoch 配列の Spy。

    全行掃引（`>=` / `<` の要素ごと比較）は ufunc として観測される。区間切り出し
    （searchsorted 系）は要素ごと演算を発行しないため計数 0 になる。
    """

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        self.sweep_counter["elements"] += max(
            (i.size for i in inputs if isinstance(i, np.ndarray)), default=0
        )
        plain = tuple(
            i.view(np.ndarray) if isinstance(i, _SweepCountingArray) else i
            for i in inputs
        )
        return getattr(ufunc, method)(*plain, **kwargs)


def _boundary_rich_model_with_sweep_spy(n_outside: int):
    """窓内 2 ティック固定＋窓外 `n_outside` ティックの model と掃引カウンタを返す。"""
    import pandas as pd

    from simulator.adapter.execution.tick_model import RealTickModel

    base = 1_704_067_200  # 2024-01-01T00:00:00Z（分アライン）
    inside = [base + 60 + 10, base + 60 + 50]  # bar minute=1 の区間 [60, 120) 内
    outside = [base + 300 + i for i in range(n_outside)]  # 遠方の別区間
    epochs = inside + outside
    frame = pd.DataFrame(
        {
            "timestamp": [np.datetime64(e, "s") for e in epochs],
            "bid": [1.0] * len(epochs),
            "ask": [1.0] * len(epochs),
            "last": [1.0] * len(epochs),
            "volume": [1.0] * len(epochs),
        }
    )
    model = RealTickModel(frame)
    counter = {"elements": 0}
    # epoch 前計算列を Spy 配列へ差し替える（値は同一・演算の観測だけを足す）。
    spy = np.asarray(model._ts_epoch).view(_SweepCountingArray)
    spy.sweep_counter = counter
    model._ts_epoch = spy
    return model, counter


def test_ticks_of_per_bar_work_does_not_grow_with_out_of_window_rows():
    # オーダーの表明（2 点）: 区間外の行数を 32 → 1024 に増やしても、1 回の ticks_of が
    #   epoch 列に発行する要素ごと演算量は増えない（全行掃引の不在）。出力は両点で同一。
    counts = {}
    outputs = {}
    for n_outside in (32, 1024):
        model, counter = _boundary_rich_model_with_sweep_spy(n_outside)
        bar = _np_int64_bar_at(1)
        outputs[n_outside] = list(model.ticks_of(bar, prev_close=1.0))
        counts[n_outside] = counter["elements"]

    assert len(outputs[32]) == 2  # 窓内 2 ティックは両点で同一に返る
    assert outputs[32] == outputs[1024]
    assert counts[32] == counts[1024], (
        f"per-bar の掃引量が frame 全長に比例している: {counts}"
    )


def test_ticks_of_materializes_no_row_it_does_not_emit(monkeypatch):
    # 発行 − 使用 = 0: frame から実体化（itertuples で走査）した行数と、返した tick 数が
    #   一致する（作ってから捨てる行が 1 行もない）。窓内外が混在する標本で測る。
    import pandas as pd

    from simulator.adapter.execution.tick_model import RealTickModel

    issued = {"rows": 0}
    real_itertuples = pd.DataFrame.itertuples

    def _spy_itertuples(self, *args, **kwargs):
        issued["rows"] += len(self)
        return real_itertuples(self, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "itertuples", _spy_itertuples)

    model = RealTickModel(_tick_frame())  # 窓内 2 件・窓外 2 件（既存の境界標本）
    ticks = list(model.ticks_of(_bar_at(0), prev_close=1.0))

    assert len(ticks) == 2
    assert issued["rows"] - len(ticks) == 0
