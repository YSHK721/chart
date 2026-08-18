"""TickModel 実装（TickModelPort・PROCESS §0.2/§7-#1・CLEAN_ARCH §6.3）。

ticks_of(bar, prev_close) -> Iterable[Tick]   # Tick = (price, bid, ask, time)

    OhlcExpandTickModel: 1 バーを O→H→L→C の 4 疑似ティックへ展開（決定論・§7-#5）。
    OpenOnlyTickModel  : 始値のみ（1 ティック）。
    EveryTickModel     : OHLC のみの入力での O→H→L→C 近似（実ティックは供給しない）。
    RealTickModel      : tick-store の実ティック frame をバー区間へ切り出す。供給元は
                         `tools/fetch_ticks_dukascopy.py`（段1 raw）→ `tools/ingest_ticks.py`
                         （段2 canonical）→ `ParquetTickRepository`。

最小骨格: spread=0 のとき bid=ask=price（実 spread は spread_model 接続時に拡張）。
Tick は標準 tuple（フレームワーク型を漏らさない）。

RealTickModel の区間算定は規則を自前で持たない（ISSUE-403 の是正）。`bar.time` の epoch
換算は `simulator.domain.bar_time.epoch_seconds`、半開 [start, end) は
`datawindow.half_open.HalfOpenEpochWindow`、足長秒は時間足台帳 `marketdata.tf_ledger` が
唯一の実体である。frame 側の timestamp → epoch 秒は共有実体 `timestamp_epoch_seconds`
（`adapter/repository/_tick_frame.py`）に委ね、構築時に 1 回だけ前計算する（`ticks_of` は
1 run につきバー本数ぶん呼ばれるため、毎回の列変換は run 全体に効く）。
"""
from __future__ import annotations

from typing import Any, Iterable

from datawindow.half_open import HalfOpenEpochWindow
from marketdata.tf_ledger import TF_BAR_SEC
from simulator.domain.bar_time import epoch_seconds
from simulator.usecase.ports import TickModelPort

# M1（1 分足）の足長秒。値を持つのは時間足台帳 `marketdata.tf_ledger` **だけ**であり、ここは
# 導出のみを行う（手書きの写しが台帳へ追随せず事故になった前例が ISSUE-261 / ISSUE-253。
# 同じ理由で台帳から導出する先例が `simulator/usecase/contact_scan/bar_window.py`）。台帳が
# ``bar_sec`` を「境界計算に使わない」と断るのは名目値を持つ上位足（1W=7日 / 1M=30日）に
# ついてであり、"1m" は再集計の原子＝定義上ちょうど 60 秒である。
_M1_SECONDS = TF_BAR_SEC["1m"]


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
    """every-tick を OHLC のみの入力で近似する（常に O→H→L→C へフォールバックする）。

    本クラスは frame を持たないため実ティックは供給しない（それは `RealTickModel`）。
    """

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        return _ohlc_ticks(bar)


def _to_domain_time(ts: Any) -> Any:
    """frame の timestamp を domain の time 型（numpy.datetime64）へ正規化する。

    pandas.Timestamp を usecase/domain へ漏らさない（Bar.time 契約: pd.Timestamp 禁止・
    numpy.datetime64 | int）。pandas.Timestamp は to_datetime64() で datetime64 化する。
    """
    to_dt64 = getattr(ts, "to_datetime64", None)
    return to_dt64() if to_dt64 is not None else ts


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
        # 関数内 import: `tick_parquet` は pyarrow / parquet 依存を持ち込むため module-level で
        # 引くと `main/__init__.py` の遅延 import 設計（既定経路に tick-store 依存を載せない）を
        # 壊す。RealTickModel の構築は real_ticks 経路でのみ起きるので、ここが最も遅い到達点。
        from simulator.adapter.repository.tick_parquet import timestamp_epoch_seconds

        # timestamp → epoch 秒は **1 回だけ**前計算する。ticks_of は 1 run につきバー本数回
        # （実データで 28097 回）呼ばれるため、毎回の列変換は run 全体に効く。
        self._ts_epoch = timestamp_epoch_seconds(self._frame["timestamp"])

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        # 半開区間 [bar.time, bar.time+足長) を epoch 秒で決定論的にスライスする。窓の定義
        # （境界の正規化・半開の向き）は共有実体だけが持つ: `epoch_seconds` が `bar.time` の
        # 表現差を吸収し、`HalfOpenEpochWindow` が [start, end) を表す。是正前はここに手書き
        # ディスパッチ（`_normalize_bar_time` / `_bar_end`）があり、``isinstance(np.int64(1), int)``
        # が **False**（実測・numpy 2.4.6）であるため comma 形式 CSV の実型（``numpy.int64``）が
        # ``np.datetime64(np.int64)`` の ``ValueError`` で落ちていた（ISSUE-403）。
        start = epoch_seconds(bar.time)
        window = HalfOpenEpochWindow(start, start + _M1_SECONDS)
        # 判定は `window.contains` と同一規則をベクトル化したものである。`.map(contains)` は
        # per-bar 呼出（1 run = 28097 回）× 行数ぶんの Python 関数呼出になるため使わない
        # （`load_ticks` は 1 run に 1 回なので `.map` で可＝呼出頻度が 4 桁違う）。
        mask = (self._ts_epoch >= window.start) & (self._ts_epoch < window.end)
        sliced = self._frame.loc[mask]
        return [
            (row.last, row.bid, row.ask, _to_domain_time(row.timestamp))
            for row in sliced.itertuples(index=False)
        ]
