"""TickModel 実装（TickModelPort・PROCESS §0.2/§7-#1・CLEAN_ARCH §6.3）。

ticks_of(bar, prev_close) -> Iterable[Tick]   # Tick = (price, bid, ask, time)

    OhlcExpandTickModel: 1 バーを O→H→L→C の 4 疑似ティックへ展開（決定論・§7-#5）。
    OpenOnlyTickModel  : 始値のみ（1 ティック）。
    EveryTickModel     : 実ティック列。OHLC のみの入力では O→H→L→C 近似へフォール
                         バック（実ティック供給は将来の Dukascopy gateway＝範囲外）。

最小骨格: spread=0 のとき bid=ask=price（実 spread は spread_model 接続時に拡張）。
Tick は標準 tuple（フレームワーク型を漏らさない）。
"""
from __future__ import annotations

from typing import Any, Iterable

from simulator.usecase.ports import TickModelPort


def _tick(price: float, bar: Any) -> tuple:
    # spread=0 の最小骨格: bid=ask=price。Tick = (price, bid, ask, time)
    half = getattr(bar, "spread", 0) / 2.0
    return (price, price - half, price + half, bar.time)


def _ohlc_ticks(bar: Any) -> Iterable[tuple]:
    for price in (bar.open, bar.high, bar.low, bar.close):
        yield _tick(price, bar)


def _ordered_ohlc_prices(
    bar: Any, order: str, prev_open: float | None, prev_close: float | None
) -> tuple:
    """4 疑似ティックの価格順を ohlc_order に従って決める（PROCESS §7 #5）。

    "ohlc": O→H→L→C（既定・従来不変）。"olhc": O→L→H→C。
    "auto": 実 MT5 1 分 OHLC の極値到達順則（2603-01 journal で実証）。
        非ドジ足は当該足方向: 強気（close>open）は安値先 O→L→H→C、弱気（close<open）は
        高値先 O→H→L→C（実体方向と逆の極値へ先に振れてから引ける）。
        ドジ足（close==open）は直前足のモメンタムを継続: 前足が陽線（prev_close>prev_open）
        なら高値先 O→H→L→C、前足が陰線なら安値先 O→L→H→C。前足不明/前足ドジは安値先を既定。
        （実証: bar 01:45 強気→L先／bar 11:37 ドジ・前足陽→H先／bar 04:25 ドジ・前足陰→L先。
        順序依存ドジ 8/8 が前足方向と一致）。
    """
    o, h, l, c = bar.open, bar.high, bar.low, bar.close
    olhc = (o, l, h, c)
    ohlc = (o, h, l, c)
    if order == "olhc":
        return olhc
    if order == "auto":
        if c > o:
            return olhc  # 強気: 安値先
        if c < o:
            return ohlc  # 弱気: 高値先
        # ドジ: 前足モメンタム継続（前足陽→高値先 / 前足陰→安値先 / 不明→安値先）
        if prev_open is not None and prev_close is not None and prev_close > prev_open:
            return ohlc
        return olhc
    return ohlc  # "ohlc"（既定）


class OhlcExpandTickModel(TickModelPort):
    """O→H→L→C 等の 4 疑似ティックへ展開する（ohlc_order で順序切替）。

    order 既定 "ohlc"（O→H→L→C）で従来挙動・既存テストと完全一致。"auto"/"olhc" は
    バー内の極値到達順を切替え、ペンディング/SL/TP の同足競合の決済順を MT5 に整合させる。
    "auto" のドジ足判定は直前足の方向を要するため、直近に処理した足の (open, close) を保持する
    （ticks_of は run 中に足を時系列で 1 回ずつ評価する前提＝決定論。order="ohlc"/"olhc" は
    前足状態を参照しないため状態保持は無害）。
    """

    def __init__(self, order: str = "ohlc") -> None:
        self._order = order
        self._prev_open: float | None = None
        self._prev_close: float | None = None

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        prices = _ordered_ohlc_prices(
            bar, self._order, self._prev_open, self._prev_close
        )
        # ドジ（close==open）の順序は直近の「非ドジ」足の方向を引き継ぐ（ドジ連鎖でも
        #   momentum が途切れない）。直前足がドジのとき prev を更新しないことで、最後に方向を
        #   持った足の (open, close) を保持する。非ドジ足では従来どおり更新する。
        if bar.close != bar.open:
            self._prev_open, self._prev_close = bar.open, bar.close
        if self._order == "auto":
            # 実 MT5 OHLC の生成ティック数は当該足の tick volume に依存する（2603-01 で実証）。
            #   tickvol >= 4: O→(極値2)→C の 4 ティックをそのまま生成（等値の隣接も別ティック。
            #     例 bar 15:32 は L==C でも 4 ティック＝約定@L→SL@C で同足決済）。
            #   tickvol < 4: 実ティックが 4 本未満ゆえ隣接等値を集約し ≈tickvol ティックにする
            #     （例 bar 23:12 tickvol=2 は O==L・H==C で 2 ティック＝約定@H 後にティックなし→
            #      持ち越し／bar 02:16 tickvol=3 は 3 ティック）。tick volume は bar.volume（<TICKVOL>）。
            tickvol = getattr(bar, "volume", 0) or 0
            if tickvol < 4:
                deduped: list = []
                for p in prices:
                    if not deduped or deduped[-1] != p:
                        deduped.append(p)
                prices = tuple(deduped)
        return [_tick(price, bar) for price in prices]


class OpenOnlyTickModel(TickModelPort):
    """始値のみ（1 ティック）。"""

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        return [_tick(bar.open, bar)]


class EveryTickModel(TickModelPort):
    """実ティック列。OHLC のみの入力では O→H→L→C 近似へフォールバックする。"""

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        return _ohlc_ticks(bar)


def _to_domain_time(ts: Any) -> Any:
    """frame の timestamp を domain の time 型（numpy.datetime64）へ正規化する。

    pandas.Timestamp を usecase/domain へ漏らさない（Bar.time 契約: pd.Timestamp 禁止・
    numpy.datetime64 | int）。pandas.Timestamp は to_datetime64() で datetime64 化する。
    """
    to_dt64 = getattr(ts, "to_datetime64", None)
    return to_dt64() if to_dt64 is not None else ts


def _normalize_bar_time(bar_time: Any) -> Any:
    """バー区間算定用に bar.time を numpy.datetime64 へ正規化する（epoch int は不変）。

    Bar.time 契約は numpy.datetime64 | int だが、CSV ローダ（CsvOHLCRepository）は
    ISO 文字列 time を「そのまま」採用するため、real_ticks 経路では bar.time が
    str / pandas.Timestamp になり得る（ISSUE-016）。区間算定は時刻演算を要するため、
    時刻系（str / numpy.datetime64 / pandas.Timestamp）は datetime64 へ寄せ、epoch int
    は算術可能なため不変で返す。pandas/numpy は本 adapter 内に閉じる。
    """
    import numpy as np

    if isinstance(bar_time, bool):  # bool は int サブクラス。時刻でないので除外
        return bar_time
    if isinstance(bar_time, int):  # epoch int はそのまま算術可能
        return bar_time
    if isinstance(bar_time, np.datetime64):
        return bar_time
    # str / pandas.Timestamp / その他時刻表現は datetime64 へ正規化する。
    return np.datetime64(bar_time)


def _bar_end(bar_time: Any) -> Any:
    """バー区間 [bar.time, bar.time+足長) の終端を返す（M1=60s 前提）。

    bar_time は _normalize_bar_time 済（numpy.datetime64 または epoch int）を前提とする。
    numpy.datetime64 なら timedelta64(60,"s")、epoch int なら +60 を加算する。足長は M1 固定。
    """
    import numpy as np

    if isinstance(bar_time, np.datetime64):
        return bar_time + np.timedelta64(60, "s")
    return bar_time + 60


class RealTickModel(TickModelPort):
    """実ティック frame からバー区間の実ティックを整形する（every-tick #4）。

    保持する canonical frame（timestamp/bid/ask/last/volume）から、当該バー区間
    [bar.time, bar.time+足長=60s) の実ティックを timestamp 昇順で
    Tick=(price=last, bid, ask, time=timestamp) へ整形する。区間 0 件は空を返す。
    pandas は本 adapter 内に隔離し usecase へ漏らさない。prev_close は契約上受けるが
    整形では未使用。
    """

    def __init__(self, frame: Any) -> None:
        # timestamp 昇順へ安定ソートする（レビュー 🟡-1・順序ハザード是正）。
        # every-tick は順序依存（最初tick=約定価格・tick列順=SL/TP/stop-out 発火順）で
        # あり、frame 行順が非ソートでも ticks_of が常に時刻順を返すことを不変条件と
        # して保証する。mergesort（安定）で同一 timestamp の相対順序を保つ。period frame
        # 一括保持中ゆえ追加メモリは実質なし。
        self._frame = frame.sort_values("timestamp", kind="mergesort")

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        ts = self._frame["timestamp"]
        # 半開区間 [bar.time, bar.time+60s) で決定論的にスライスする。bar.time は CSV 由来で
        # ISO 文字列になり得る（ISSUE-016）ため区間算定用に datetime64 へ正規化する。
        bar_start = _normalize_bar_time(bar.time)
        mask = (ts >= bar_start) & (ts < _bar_end(bar_start))
        sliced = self._frame.loc[mask]
        return [
            (row.last, row.bid, row.ask, _to_domain_time(row.timestamp))
            for row in sliced.itertuples(index=False)
        ]
