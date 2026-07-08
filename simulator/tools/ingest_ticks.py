"""raw(Dukascopy)→canonical 変換と tick-store ingest（2 段パイプラインの段2）。

段1（raw landing）は fetch_ticks_dukascopy.py が担当し、Dukascopy ネイティブ列
(timestamp/bidPrice/askPrice/bidVolume/askVolume) を不変アーカイブとして保存する。
本モジュールはその raw を tick-store の canonical スキーマ(TICK_COLUMNS:
timestamp/bid/ask/last/volume) へ変換し ParquetTickRepository へ ingest する。

技術隔離: pandas は本ファイル内に閉じる。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from simulator.adapter.repository._tick_frame import TICK_COLUMNS
from simulator.adapter.repository.tick_parquet import ParquetTickRepository
from simulator.domain.exceptions import MissingBarError

# Dukascopy raw frame の必須列（段1 fetch が保存するネイティブ列）。
RAW_COLUMNS = ("timestamp", "bidPrice", "askPrice", "bidVolume", "askVolume")


def to_canonical_ticks(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Dukascopy raw frame を tick-store の canonical frame へ変換する。

    マッピング:
        bid    = bidPrice
        ask    = askPrice
        last   = (bid + ask) / 2   ← quote feed には約定値がないため last=mid 規約
        volume = bidVolume + askVolume
        timestamp は naive UTC へ正規化する。

    timestamp 正規化（🟡-1 store 契約）:
        tick-store は naive UTC 固定の契約（synth_ticks が naive 生成・
        ParquetTickRepository.load_ticks docstring が「保存 timestamp は naive UTC
        固定」を宣言）。raw が tz-aware UTC でも canonical は tz_convert("UTC") 後に
        tz_localize(None) で tz-naive datetime64 へ落とす（全 UTC のため情報損失なし）。
        既に naive ならそのまま。これにより load_ticks の bound 要求が保存ソースに
        よらず naive UTC に統一される（store 契約の一貫性）。

    出力列は TICK_COLUMNS(timestamp/bid/ask/last/volume) 準拠。
    入力に RAW_COLUMNS が欠ける場合は MissingBarError へ翻訳する（生 KeyError を漏らさない）。
    """
    missing = [c for c in RAW_COLUMNS if c not in raw_df.columns]
    if missing:
        raise MissingBarError(
            f"raw 必須列が不足しています: {missing}",
            context={"missing": missing, "columns": list(raw_df.columns)},
        )

    timestamp = raw_df["timestamp"]
    if getattr(timestamp.dt, "tz", None) is not None:
        # tz-aware → UTC 揃え後に tz を剥がし naive datetime64 へ（全 UTC＝値不変）。
        timestamp = timestamp.dt.tz_convert("UTC").dt.tz_localize(None)

    bid = raw_df["bidPrice"]
    ask = raw_df["askPrice"]
    out = pd.DataFrame(
        {
            "timestamp": timestamp,
            "bid": bid,
            "ask": ask,
            "last": (bid + ask) / 2.0,
            "volume": raw_df["bidVolume"] + raw_df["askVolume"],
        }
    )
    return out[list(TICK_COLUMNS)]


def ingest_raw_parquet(
    raw_path: Any,
    store_root: Any,
    symbol: str,
    mode: str = "overwrite",
):
    """raw parquet を読み→to_canonical_ticks→ParquetTickRepository.write_ticks する。

    最小実装は frame 経由（raw 全体を読み変換して 1 frame で write_ticks に渡す）。
    write_ticks 内部は日別 groupby ルーティングで日分割 part.parquet を生成する。

    返値: ParquetTickRepository.write_ticks の TickWriteResult（書込日数・行数）。
    """
    raw_df = pd.read_parquet(Path(raw_path))
    canonical = to_canonical_ticks(raw_df)
    repo = ParquetTickRepository(root=store_root)
    return repo.write_ticks(symbol, canonical, mode=mode)
