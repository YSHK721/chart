"""moving_averages の増分器（ISSUE-233 S1・内部設計_latest増分計算.md §5.2/§5.3）。

真因の除去:
    従来の ``latest`` は末尾 1 点を得るために ``adapter.compute`` を全件で呼び、窓全体
    （実測 1386 本）を再計算していた。本増分器は **確定バーまでの MA バッファを状態として
    保持**し、形成中バー 1 本ぶんだけ漸化を進める。所要は窓長に依らず一定になる。

参照実装（無改変・計算式を写さない）:
    MA の計算は moving_averages src の公開バッファ関数がそのまま行う。
      - sma / ema / smma : ``*_on_buffer`` の ``prev_calculated`` 契約（MQL 由来）。
        ``prev_calculated`` から続きを計算する経路が full の漸化をそのまま継続するため、
        結果は full と bit 一致する（実測: max_dev = 0）。
      - lwma            : ``linear_weighted_ma_on_buffer_stateful``（ISSUE-233 で追加）。
        classic の ``prev_calculated>0`` 分岐は走行和を窓から再構築し full と bit 一致
        しないため（実測 max_dev 2.1e-09）、走行和を授受する入口を用いる。
    系列 metadata（名前・色・描画ヒント）も書き写さず、``incremental_state`` が実計算から
    採取した骨格を使う。本モジュールが自前で持つのは「末尾何点目をどの時刻へ置くか」という
    ``add_moving_averages`` の emit 規約（offset シフト・NaN 除外・warm-up マスク）だけで、
    その一致は tests/latest/test_moving_averages_incremental.py が max_dev = 0 で固定する。

対象外（``prepare`` が None を返し、従来の full 経路へ落ちる＝挙動不変）:
    - ``smoothing_type != "none"``: 平滑化は MA 系列に対する pandas rolling/ewm であり、
      末尾だけを bit 一致で求める手段が src の公開面に無い（開始点を変えると値が動く）。
    - ``time_column`` 指定あり・未知 ma_type・``length < 2``。
    - 実効本数が ``length + 3`` 未満（warm-up 直後。``prev_calculated`` 契約の継続開始位置が
      シード領域へ食い込む本数では使わない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from common.applied_price import SOURCE_TO_APPLIED, applied_price
from common_view.lwc_adapter import resolve_times
from adapter.compute.incremental._emit import tail_points_offset

from adapter.compute.call_binding import indicator_src

# 実効本数の下限（= length + _MIN_MARGIN）。``prev_calculated`` 契約の継続開始位置
# （prev_calculated-1）がシード領域（先頭 length 本）を越えることを保証する。
_MIN_MARGIN = 3


@dataclass(frozen=True)
class _Request:
    """1 リクエストぶんの入力（実効系列＝wait_for_close 適用後）。"""

    prices: np.ndarray   # 実効価格（昇順・長さ n。末尾は形成中バー由来のことがある）
    times: np.ndarray    # 実効時刻（UNIX 秒 int64・長さ n）
    n: int
    ma_type: str
    length: int
    offset: int
    valid_from: int      # warm-up マスク開始（ema は 0・他は length-1）


@dataclass(frozen=True)
class _State:
    """確定プレフィクスの状態（不変・emit では書き換えない）。"""

    prices: np.ndarray   # 確定実効価格（長さ m）
    buffer: np.ndarray   # 未マスクの MA バッファ（長さ m）
    lwma: Any            # LwmaState（lwma のみ）/ None
    m: int


class MovingAveragesIncrementer:
    """``Incrementer`` 実装（moving_averages）。"""

    # --- src 公開関数の解決（遅延・プロセス内で使い回す） ---
    _src: Any = None

    def _module(self) -> Any:
        if MovingAveragesIncrementer._src is None:
            MovingAveragesIncrementer._src = indicator_src("moving_averages")
        return MovingAveragesIncrementer._src

    def _on_buffer(self, ma_type: str):
        """ma_type → src 公開バッファ関数（lwma は stateful を別途使う）。"""
        src = self._module()
        return {
            "sma": src.simple_ma_on_buffer,
            "ema": src.exponential_ma_on_buffer,
            "smma": src.smoothed_ma_on_buffer,
        }[ma_type]

    # ------------------------------------------------------------------ #
    # prepare
    # ------------------------------------------------------------------ #
    def prepare(self, df: Any, params: dict[str, Any]) -> "_Request | None":
        try:
            return self._prepare(df, params)
        except (KeyError, ValueError, TypeError):
            # 入力不正（時刻解決不能・非数値パラメータなど）は従来経路へ委ねる。エラー種別の
            # 翻訳は adapter.compute（参照実装）が唯一の担当であり、ここで再現しない。
            # それ以外の例外（増分器自身の不具合）は握り潰さず送出する（無言の full 退行＝
            # 劣化の不可視化を作らない）。
            return None

    def _prepare(self, df: Any, params: dict[str, Any]) -> "_Request | None":
        if params.get("time_column") is not None:
            return None
        ma_type = str(params.get("ma_type", "ema")).lower()
        src = self._module()
        if ma_type not in set(src.MA_TYPES):
            return None
        if str(params.get("smoothing_type", "none")).lower() != "none":
            return None
        length = int(round(float(params.get("length", 9))))
        if length < 2:
            return None
        offset = int(round(float(params.get("offset", 0))))

        kind = SOURCE_TO_APPLIED.get(str(params.get("source", "close")).lower())
        if kind is None:
            return None
        lower = {str(c).lower(): c for c in df.columns}
        cols = {}
        for name in ("open", "high", "low", "close"):
            if name not in lower:
                return None
            cols[name] = df[lower[name]].to_numpy(dtype=np.float64)
        prices = applied_price(kind, cols["open"], cols["high"], cols["low"], cols["close"])
        # UNIX 秒（int）への変換は fake_chart._line_points と同一規約
        # （datetime64[s] へ切り下げ → int64）。変換は numpy 上で行う（既に datetime64 の
        # 系列へ pd.to_datetime を再適用すると 1386 本で 0.73ms かかり 1 ステップの所要を
        # 支配する＝実測。resolve_times は datetime の Series を返す契約のため通常は不要）。
        resolved = resolve_times(df, None)
        stamps = resolved.to_numpy()
        if not np.issubdtype(stamps.dtype, np.datetime64):
            stamps = pd.to_datetime(resolved).to_numpy()
        times = stamps.astype("datetime64[s]").astype("int64")

        # 確定待ち（add_moving_averages と同一規約）: 最終足を計算対象から除外する。
        if params.get("wait_for_close", True) and len(prices) > 1:
            prices = prices[:-1]
            times = times[:-1]
        n = int(prices.size)
        if n < length + _MIN_MARGIN:
            return None

        valid_from = 0 if ma_type in src.MA_FROM_ZERO else length - 1
        return _Request(
            prices=prices, times=times, n=n, ma_type=ma_type,
            length=length, offset=offset, valid_from=valid_from,
        )

    # ------------------------------------------------------------------ #
    # build / adapt（状態の構築と前進）
    # ------------------------------------------------------------------ #
    def _run(
        self, req: "_Request", prices: np.ndarray, buffer: np.ndarray,
        size: int, prev_calculated: int, lwma: Any,
    ) -> Any:
        """``[prev_calculated, size)`` を計算して ``buffer`` を埋め、更新後の lwma 状態を返す。"""
        if req.ma_type == "lwma":
            _, state = self._module().linear_weighted_ma_on_buffer_stateful(
                size, 0, req.length, prices, buffer, lwma
            )
            return state
        self._on_buffer(req.ma_type)(size, prev_calculated, 0, req.length, prices, buffer)
        return None

    def build(self, req: "_Request") -> "_State":
        """確定プレフィクス（末尾 1 本を除く全件）から状態を構築する（初回のみ）。"""
        m = req.n - 1
        prices = req.prices[:m]
        buffer = np.zeros(m, dtype=np.float64)
        lwma = self._run(req, prices, buffer, m, 0, None)
        return _State(prices=prices, buffer=buffer, lwma=lwma, m=m)

    def adapt(self, state: "_State", req: "_Request") -> "_State | None":
        """既存状態を流用する（必要なら確定バーぶん前進した新しい状態を返す）。

        流用可否は「確定プレフィクスの実効価格が完全一致するか」だけで決める（MA は
        prices と (ma_type, length) のみの関数であり、後者はキーに含まれる）。左端が動いた窓・
        別データセット・過去分の訂正はここで不一致になり、再構築へ落ちる。
        """
        m_conf = req.n - 1
        if state.m == m_conf:
            if np.array_equal(state.prices, req.prices[:m_conf]):
                return state
            return None
        if state.m > m_conf:
            # 長い窓の状態を短い窓へ流用する（buffer[i] は prices[0..i] のみに依存）。
            # lwma は走行和が state.m 時点のものであり m_conf へ巻き戻せないため再構築する。
            if req.ma_type == "lwma":
                return None
            if not np.array_equal(state.prices[:m_conf], req.prices[:m_conf]):
                return None
            return _State(
                prices=state.prices[:m_conf], buffer=state.buffer[:m_conf],
                lwma=None, m=m_conf,
            )
        # state.m < m_conf: 確定した分だけ前進する（1 回の呼出で何本でも進む）。
        if not np.array_equal(state.prices, req.prices[:state.m]):
            return None
        prices = req.prices[:m_conf]
        buffer = np.zeros(m_conf, dtype=np.float64)
        buffer[:state.m] = state.buffer
        lwma = self._run(req, prices, buffer, m_conf, state.m, state.lwma)
        return _State(prices=prices, buffer=buffer, lwma=lwma, m=m_conf)

    # ------------------------------------------------------------------ #
    # emit（非破壊・末尾 K 点）
    # ------------------------------------------------------------------ #
    def emit(
        self, state: "_State", req: "_Request", skeleton: list, k: "int | None"
    ) -> "list[dict] | None":
        """確定状態＋形成中バーから末尾 K 点を組む。状態は読むだけ（§5.3.2）。"""
        if k is None or k <= 0:
            return None
        # 平滑化なしの moving_averages は "MA" 1 系列のみ。骨格が想定と違う形なら扱わない。
        if len(skeleton) != 1 or skeleton[0].get("name") != "MA":
            return None
        m_conf = req.n - 1
        if state.m != m_conf:
            return None

        # 形成中バー 1 本ぶんだけ進める（state は書き換えず作業バッファへ複製する）。
        buffer = np.empty(req.n, dtype=np.float64)
        buffer[:m_conf] = state.buffer
        buffer[m_conf] = 0.0
        self._run(req, req.prices, buffer, req.n, m_conf, state.lwma)

        # add_moving_averages の emit 規約: values[i] を times[i+offset] へ置く（範囲外は捨てる）。
        # NaN（warm-up マスク・未計算）は点を出さない。
        i_high = req.n - 1 - max(req.offset, 0)
        i_low = max(0, -req.offset, req.valid_from)
        # 末尾 K 点の組み立て（NaN 除外・時刻の UNIX 秒化）は _emit が唯一実装（ISSUE-273）。
        points = tail_points_offset(
            buffer, req.times, i_high=i_high, i_low=i_low, offset=req.offset, k=k)
        return [{**skeleton[0], "data": points}]
