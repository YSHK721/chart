"""DukascopyCandleSource — Dukascopy 実装（``dukascopy-python`` を隔離する adapter）。

無料・アカウント不要の Dukascopy ヒストリカルデータを :class:`CandleSource` ポートへ適合させる。
ライブラリ依存（``dukascopy_python``）はこのモジュールに限定し、利用側へ漏らさない。

時刻:
    Dukascopy は UTC。candles の ``time`` は解像度非依存に ``int(pd.Timestamp(v).timestamp())``
    で UNIX 秒へ変換する（pandas3 で ``astype // 10**9`` は誤り）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, List

import pandas as pd

import dukascopy_python
from dukascopy_python.instruments import INSTRUMENT_IDX_ASIA_E_N225JAP

from marketdata.port import Candle

# JP225（日経225）= Dukascopy 銘柄 "E_N225Jap"。
JP225 = INSTRUMENT_IDX_ASIA_E_N225JAP

# 足種名 → ライブラリ INTERVAL 定数（利用側の CLI 等が参照する）。
INTERVALS = {
    "day_1": dukascopy_python.INTERVAL_DAY_1,
    "hour_4": dukascopy_python.INTERVAL_HOUR_4,
    "hour_1": dukascopy_python.INTERVAL_HOUR_1,
    "min_30": dukascopy_python.INTERVAL_MIN_30,
    "min_15": dukascopy_python.INTERVAL_MIN_15,
    "min_5": dukascopy_python.INTERVAL_MIN_5,
    "min_1": dukascopy_python.INTERVAL_MIN_1,
}

# 気配側名 → ライブラリ OFFER_SIDE 定数（INTERVALS と対称・利用側のベンダ非依存化用）。
# 利用側は "bid"/"ask" の文字列で気配側を指定し、ベンダ定数はこの境界で隔離する。
OFFER_SIDES = {
    "bid": dukascopy_python.OFFER_SIDE_BID,
    "ask": dukascopy_python.OFFER_SIDE_ASK,
}

# 一次識別子（TIMEFRAME_RULES キー系 "5m"/"1h"/"1D" …）→ ライブラリ INTERVAL 定数（§10.3 M-1）。
# enabler④（足種の命名統一）: 呼出面の一次識別子を marketdata.resample.TIMEFRAME_RULES の
# キー系へ統一し、ベンダ INTERVAL 定数へのベンダ固有変換はこの adapter 境界に閉じる。既存
# INTERVALS（"min_5"/"hour_1"）直利用の呼出は不変のまま、新たに "5m" 系での解決経路を追加する
# （後方互換・両系統サポート）。"1W"/"1M" は含めない。dukascopy_python は INTERVAL_WEEK_1 /
# INTERVAL_MONTH_1 を持つが、本プロジェクトの週足/月足は 1 分足原子から resample で導出する
# 設計（marketdata.resample.TIMEFRAME_RULES の "1W"="W-FRI" / "1M"="ME"・rollup 生成）であり、
# 既存 INTERVALS も週足/月足を持たない。よって本変換表は「直接 fetch する足種」のみに限定し、
# 既存 INTERVALS のキー集合（min_1..day_1）と 1:1 対応させる（§4 ロールアップ設計と整合）。
TIMEFRAME_INTERVALS = {
    "1m": dukascopy_python.INTERVAL_MIN_1,
    "5m": dukascopy_python.INTERVAL_MIN_5,
    "15m": dukascopy_python.INTERVAL_MIN_15,
    "30m": dukascopy_python.INTERVAL_MIN_30,
    "1h": dukascopy_python.INTERVAL_HOUR_1,
    "4h": dukascopy_python.INTERVAL_HOUR_4,
    "1D": dukascopy_python.INTERVAL_DAY_1,
}


def interval_for_timeframe(timeframe: str) -> Any:
    """足種コードをライブラリ INTERVAL 定数へ解決する（両系統サポート・§10.3 M-1）。

    一次識別子（``"5m"/"1h"/"1D"`` …＝:data:`TIMEFRAME_INTERVALS` キー）を優先解決し、旧系統名
    （``"min_5"/"hour_1"`` …＝:data:`INTERVALS` キー）も後方互換で解決する。いずれにも無い未知
    コードは ``KeyError``（暗黙のフォールバックを設けない）。
    """
    if timeframe in TIMEFRAME_INTERVALS:
        return TIMEFRAME_INTERVALS[timeframe]
    return INTERVALS[timeframe]


def _to_candles(df: pd.DataFrame) -> List[Candle]:
    """UTC OHLCV DataFrame を candles へ変換する（純粋・time 昇順・同一 time は後勝ち）。

    ``volume`` 列があれば Candle.volume へ抽出する（enabler①）。列が無い／NaN は ``0.0``。
    """
    vol = df["volume"] if "volume" in df.columns else None
    by_time: dict[int, Candle] = {}
    for i, (ts, o, h, low, c) in enumerate(
        zip(df.index, df["open"], df["high"], df["low"], df["close"])
    ):
        t = int(pd.Timestamp(ts).timestamp())
        raw_v = vol.iloc[i] if vol is not None else 0.0
        v = 0.0 if pd.isna(raw_v) else float(raw_v)  # 列不在 / NaN は 0.0（欠損 volume）。
        by_time[t] = {
            "time": t,
            "open": float(o),
            "high": float(h),
            "low": float(low),
            "close": float(c),
            "volume": v,
        }
    return [by_time[t] for t in sorted(by_time)]


def fetch_ticks_since(
    cursor_ms: int,
    *,
    instrument: str = JP225,
    limit: int = 30_000,
) -> List[tuple]:
    """``cursor_ms`` より後の増分 tick を ``(unix_ms, bid, ask)`` 昇順で返す（ベンダ隔離）。

    freeserv 増分カーソル API（``dukascopy_python._fetch``・公式ライブウィジェットと同じ
    endpoint）の薄いラッパ。``last_update=cursor_ms`` を渡し、直近数秒分（数 KB）を 1 接続で
    取得する。戻りは ``cursor_ms`` より厳密に後の行のみ（重複ダウンロード・境界重複を排する）。

    private API 依存（``_fetch`` はライブラリ非公開）である点に注意する。この呼び方
    （instrument / interval=TICK / offer_side=BID / last_update / limit）は
    ``prototype_260707-01/server.py`` の ``_poll_loop`` で実機ポーリングし実測済みである
    （feed 側 lag 3.8〜5.5s・fetch 1.2s・5 秒周期で枯渇なし＝12 秒固定遅延の根拠）。

    Args:
        cursor_ms: この UNIX ミリ秒より後の tick のみ返す（0 なら freeserv 既定の直近窓）。
        instrument: 銘柄（既定 JP225＝日経225）。
        limit: 1 リクエストの最大行数（既定 30,000）。

    Returns:
        ``(unix_ms, bid, ask)`` の list（``unix_ms > cursor_ms`` のみ・昇順）。
    """
    rows = dukascopy_python._fetch(
        instrument=instrument,
        interval=dukascopy_python.INTERVAL_TICK,
        offer_side=dukascopy_python.OFFER_SIDE_BID,
        last_update=cursor_ms,
        limit=limit,
    )
    return [
        (int(r[0]), float(r[1]), float(r[2]))
        for r in rows
        if int(r[0]) > cursor_ms
    ]


class DukascopyCandleSource:
    """Dukascopy から OHLC candles を取得する :class:`CandleSource` 実装。

    銘柄・足種・気配側は構築時に固定し、:meth:`fetch_candles` は期間のみを受ける
    （ポートの呼び出し面にベンダ固有を出さない）。クリーニング（外れ値補正）は
    ``marketdata.repair_ohlc_outliers`` として呼び出し側で明示的に合成する（観測可能に保つ）。
    """

    def __init__(
        self,
        *,
        instrument: str = JP225,
        interval: Any = dukascopy_python.INTERVAL_DAY_1,
        offer_side: Any = dukascopy_python.OFFER_SIDE_BID,
    ) -> None:
        self._instrument = instrument
        self._interval = interval
        self._offer_side = offer_side

    def fetch_candles(self, start: datetime, end: datetime) -> List[Candle]:
        """``[start, end)`` の candles を time 昇順で返す（データなしは空 list）。"""
        df = dukascopy_python.fetch(
            self._instrument, self._interval, self._offer_side, start, end
        )
        if df is None or df.empty:
            return []
        return _to_candles(df)


class DukascopyTickSource:
    """Dukascopy 実 tick を取得する :class:`TickSource` 実装（enabler②）。

    ``fetch_ticks_dukascopy.fetch_range`` のロジック（日次チャンク・resilient 取得・連結）を
    移管する。``INTERVAL_TICK`` の隔離・銘柄固定はここに閉じる。

    H-2: 戻り DataFrame は ``timestamp`` を**列**に持つ（``reset_index`` 済・列名 ``"timestamp"``）。
    ``ingest.RAW_COLUMNS``（timestamp/bidPrice/askPrice/bidVolume/askVolume）契約へ直接適合する。

    H-3: ``offer_side`` 単一指定は持たない。raw tick は気配側に依らず bidPrice/askPrice 両列を
    含むため、両列を常に返し last=mid=(bid+ask)/2 算出を保全する。気配側の選択は port の責務外。
    """

    def __init__(self, *, instrument: Any = JP225) -> None:
        self._instrument = instrument

    def fetch_ticks(self, start: datetime, end: datetime) -> pd.DataFrame:
        """``[start, end)`` の raw tick を日次チャンクで取得し timestamp 列の DataFrame で返す。

        取得失敗日はスキップして継続する（resilient・進捗ログ）。データなしは空 DataFrame。
        """
        frames: list[pd.DataFrame] = []
        day = start
        while day < end:
            nxt = min(day + timedelta(days=1), end)
            try:
                df = dukascopy_python.fetch(
                    self._instrument, dukascopy_python.INTERVAL_TICK,
                    dukascopy_python.OFFER_SIDE_BID, day, nxt,
                )
            except Exception as exc:  # noqa: BLE001 (取得失敗日はスキップ・継続)
                print(f"  WARN {day:%Y-%m-%d}: fetch失敗 skip ({exc})", flush=True)
                day = nxt
                continue
            n = 0 if df is None else len(df)
            if n:
                frames.append(df)
            print(
                f"  {day:%Y-%m-%d}: {n} ticks (累計 {sum(len(f) for f in frames)})",
                flush=True,
            )
            day = nxt
        if not frames:
            return pd.DataFrame()
        # 連結し timestamp 昇順。H-2: index(=timestamp) を列へ出す（reset_index 済・列名 timestamp）。
        out = pd.concat(frames).sort_index()
        return out.reset_index().rename(columns={"index": "timestamp"})
