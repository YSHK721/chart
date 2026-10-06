"""選んだ足（Period）の足を 1 分足から作る（2026-10-06・依頼者承認）。

選んだ足で判定する run は、MT5 と同じく**判定はチャートの足・約定はティック**で行う:
    * 戦略は選んだ足ごとに 1 回だけ判定し、指標は選んだ足の系列で引く。
    * 約定・SL/TP・口座の評価は 1 分足（とその足の途中の評価点）で行う。

判定する 1 分足は、戦略が名乗る建値基準（`EntryPriceBasisPort`）が決める:
    * ``"close"``（その足の終値で判定する）→ 期間の**最後**の 1 分足。期間の終値が確定した瞬間。
    * それ以外（足の始まりで判定する）→ 期間の**最初**の 1 分足。
  期間の始まりで「出来上がった足」の終値を読ませると先読みになる（レビュー実測 2026-10-06）。

足の区切りは marketdata の再集計（`marketdata.resample.resample_ohlc_tf`）が唯一の所有者
であり、ここで書き直さない（日足以上はブローカー暦日＝MT5 のサーバ日・4h はセッション日起点・
他は UTC 床）。各期間の UTC 始端も同じモジュールの `period_utc_start` から引く。

気配幅だけは足の集約規則（最小値）を使わず、**判定する 1 分足の値**を書く。戦略は判定の瞬間の
クォート（ask = bid + 気配幅）で発注価格を作るので、期間全体の最小値を渡すと先読みになる。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from marketdata.resample import period_utc_start, resample_ohlc_tf
from simulator.domain.bar_time import epoch_seconds_of_datetime64_array
from simulator.usecase.bar_times import bar_epoch_seconds

#: 価格の列（足の集約規則は marketdata の値列台帳）。
_PRICE_COLUMNS = ("open", "high", "low", "close")
#: 気配幅の列名（判定する 1 分足の値を書く）。
_SPREAD = "spread"
#: 出来高の列名（判定に使う EA は無い。データに無ければ 0 を書く）。
_VOLUME = "volume"


def period_frame(
    frame: pd.DataFrame, row_times: Sequence[int], tf: str, *, decide_at_end: bool
) -> pd.DataFrame:
    """1 分足の DataFrame（行の時刻は ``row_times``・epoch 秒）を ``tf`` の足へまとめる。

    1 分足の Bar 列を作らずにフレーム段でまとめる（全行の Bar を作ってから捨てない）。
    """
    index = pd.to_datetime(np.asarray(row_times, dtype=np.int64), unit="s")
    prices = pd.DataFrame(
        {name: frame[name].to_numpy(dtype=float) for name in _PRICE_COLUMNS}, index=index
    )
    if _VOLUME in frame.columns:
        prices[_VOLUME] = frame[_VOLUME].to_numpy(dtype=float)
    out = resample_ohlc_tf(prices, tf)
    if _SPREAD in frame.columns:
        # 同じ区切りで「最初の値・最後の値」を取るため、気配幅を価格の列に載せてまとめる
        #   （区切りの規則を 2 つ書かない）。
        spread = frame[_SPREAD].to_numpy(dtype=float)
        as_prices = resample_ohlc_tf(
            pd.DataFrame({name: spread for name in _PRICE_COLUMNS}, index=index), tf
        )
        out[_SPREAD] = as_prices["close" if decide_at_end else "open"].astype(int)
    return out


def label_epochs(frame: pd.DataFrame) -> "list[int]":
    """まとめた足のラベル時刻（epoch 秒・CSV の ``date`` 列を UTC として読んだ値と同じ）。"""
    # epoch 秒化の式は domain の単一ソースを使う（ここで datetime64 を cast しない）。
    return epoch_seconds_of_datetime64_array(frame.index.values)


def write_marketdata_csv(frame: pd.DataFrame, path: Path) -> Path:
    """まとめた足を marketdata 形式（先頭列 ``date``・naive UTC）の CSV に書く。"""
    out = frame.copy()
    if _VOLUME not in out.columns:
        out[_VOLUME] = 0.0
    columns = [*_PRICE_COLUMNS, _VOLUME] + ([_SPREAD] if _SPREAD in out.columns else [])
    out = out.loc[:, columns]
    out.insert(0, "date", out.index.strftime("%Y-%m-%d %H:%M:%S"))
    out.to_csv(path, index=False)
    return path


@dataclass(frozen=True)
class PeriodOwnership:
    """run の 1 分足と選んだ足の対応（どの 1 分足がどの選んだ足に属し、どこで判定するか）。

    ``first`` / ``last``  run の 1 分足が属する選んだ足の番号の範囲（全履歴の選んだ足の番号）。
                          run が表示・判定する選んだ足は ``[first, last]`` の連続区間である。
    ``owners``            1 分足ごとの選んだ足の番号（``first`` を 0 とする番号）。
    ``starts`` / ``ends`` 選んだ足ごとの最初・最後の 1 分足の位置（``first`` を 0 とする番号順）。
    ``positions``         1 分足ごとの「この足で判定する選んだ足の番号」（判定しない足は None）。
    """

    first: int
    last: int
    owners: np.ndarray
    starts: "list[int]"
    ends: "list[int]"
    positions: "list[int | None]"


def ownership(
    m1_bars: Sequence[Any], period_labels: Sequence[int], tf: str, *, decide_at_end: bool
) -> PeriodOwnership:
    """run の 1 分足を全履歴の選んだ足（ラベル時刻 ``period_labels``・epoch 秒）へ割り当てる。

    選んだ足を Bar にする前に割り当てる（Bar にするのは run の区間 ``[first, last]`` だけ）。

    選んだ足 k の期間は ``[period_utc_start(k), period_utc_start(k+1))``。窓の境界は 1 分足の
    側が決める（選んだ足をラベル時刻で窓に絞らない＝期間の途中で窓が切れても、残りの
    1 分足を前の足へ入れない）。
    """
    starts_utc = np.fromiter(
        (
            int(period_utc_start(tf, pd.Timestamp(t, unit="s")).timestamp())
            for t in period_labels
        ),
        dtype=np.int64,
        count=len(period_labels),
    )
    owner = np.searchsorted(
        starts_utc, np.asarray(bar_epoch_seconds(m1_bars), dtype=np.int64), side="right"
    ) - 1
    if len(owner) == 0:
        return PeriodOwnership(0, -1, owner, [], [], [])
    if int(owner.min()) < 0:
        raise ValueError("最初の選んだ足より前の 1 分足があります（選んだ足は同じ 1 分足から作る）")
    first, last = int(owner[0]), int(owner[-1])
    local = owner - first
    change = np.flatnonzero(np.diff(local)) + 1
    starts = [0, *change.tolist()]
    ends = [*(change - 1).tolist(), len(local) - 1]
    if local[starts].tolist() != list(range(last - first + 1)):
        raise ValueError("1 分足を 1 本も持たない選んだ足が run の区間の途中にあります")
    positions: "list[int | None]" = [None] * len(local)
    for k, i in enumerate(ends if decide_at_end else starts):
        positions[i] = k
    return PeriodOwnership(first, last, local, starts, ends, positions)
