"""tick frame の adapter 内部ヘルパー（列検証・partition 列付与・日付述語構築）。

OHLC 用 ``_ohlc_frame`` とは別系統（tick は timestamp/bid/ask/last/volume の列構成・
hive partition <root>/<symbol>/year=/month=/day= を前提とする）。pandas を技術
ドライバとして本ファイル内に隔離する（usecase/domain にのみ論理依存）。

例外翻訳方針（CLEAN_ARCH §6）:
    TICK_COLUMNS 欠損   → MissingBarError
    timestamp 非昇順    → TimeOrderError
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from simulator.domain.exceptions import DataError, MissingBarError, TimeOrderError

# 設計の列定義（synth_ticks.TICK_COLUMNS と一致）。
TICK_COLUMNS = ("timestamp", "bid", "ask", "last", "volume")


def validate_tick_columns(df: pd.DataFrame) -> pd.DataFrame:
    """TICK_COLUMNS 必須列と timestamp 昇順を検証する（不正は内側例外へ翻訳）。"""
    missing = [c for c in TICK_COLUMNS if c not in df.columns]
    if missing:
        raise MissingBarError(
            f"必須列が不足しています: {missing}",
            context={"missing": missing, "columns": list(df.columns)},
        )

    ts = pd.to_datetime(df["timestamp"])
    if not ts.is_monotonic_increasing:
        raise TimeOrderError(
            "timestamp が昇順ではありません",
            context={"first": str(ts.iloc[0]), "last": str(ts.iloc[-1])},
        )
    return df


def timestamp_epoch_seconds(timestamps: pd.Series) -> pd.Series:
    """timestamp 列 → UTC epoch 秒（int64）。tick フレームの**唯一の変換実体**（ISSUE-406）。

    事前条件: ``timestamps`` は ``datetime64`` 系（任意解像度 ms/us/ns・naive /
        tz-aware のいずれでもよい）の列で、NaT を含まない。
    事後条件: UTC 基準の epoch 秒（int64 Series）。naive は **UTC** とみなし
        （窓境界・`bar.time` と同じ共有規則）、秒未満は floor。結果は dtype 解像度に
        依存しない（``astype("int64")`` の直接除算は解像度前提を持ち込むため使わない。
        是正前の CLI はこれで ms 列に対し 10^6 倍ずれた）。
    例外: ``datetime64`` 系でない列・NaT を含む列は ``DataError``。どちらも黙って
        誤った秒（int 列なら 10^9 倍ずれ・NaT なら int64 最小値）になる入力であり、
        ISSUE-406 と同型の「例外なしの桁ずれ」を許さないため明示拒否する。
        **空列は dtype を問わず受理**する（値が 1 つも無い列に「黙って誤る秒」は
        存在しない。`load_ticks` の空窓の返り値は 0 行 object dtype ＝ ISSUE-402 で
        文書化済みの既存契約であり、これを拒否すると空窓の呼出側が壊れる）。
    """
    if len(timestamps) == 0:
        return pd.Series([], dtype="int64", index=timestamps.index)
    if not pd.api.types.is_datetime64_any_dtype(timestamps):
        raise DataError(
            f"timestamp 列が datetime64 系ではありません: {timestamps.dtype}",
            context={"dtype": str(timestamps.dtype)},
        )
    if timestamps.isna().any():
        raise DataError(
            "timestamp 列に NaT が含まれています",
            context={"n_nat": int(timestamps.isna().sum())},
        )
    return (
        pd.to_datetime(timestamps, utc=True)  # naive は UTC とみなす（共有規則）
        .dt.tz_localize(None)
        .astype("datetime64[s]")  # 秒へ floor（dtype 解像度 ms/us/ns に依存しない）
        .astype("int64")
    )


def with_partition_columns(df: pd.DataFrame) -> pd.DataFrame:
    """timestamp から year/month/day の hive partition 列を付与する。"""
    out = df.copy()
    ts = pd.to_datetime(out["timestamp"])
    out["year"] = ts.dt.year
    out["month"] = ts.dt.month
    out["day"] = ts.dt.day
    return out


def _date_predicate(start_epoch: int, end_epoch: int) -> list[tuple[int, int, int]]:
    """半開区間 ``[start_epoch, end_epoch)``（epoch 秒）を覆う (year, month, day) を UTC で列挙する。

    事前条件: 引数は **epoch 秒（int）**。境界の時刻表現からの正規化は呼出側
        （`tick_parquet.load_ticks`）が `simulator.domain.bar_time.epoch_seconds` で
        行う（正規化点を入口 1 箇所に置き、本関数は日列挙だけを担う）。
    事後条件: hive partition (year, month, day) を UTC 基準・昇順・重複なしで返す。
        ``end_epoch`` がちょうど日境界 00:00:00 のとき end 当日を含めない（半開）。
        ``start_epoch >= end_epoch``（空窓）のときは空リストを返す
        （`datawindow.half_open.HalfOpenEpochWindow` が start > end を空窓として扱うのと
        同じ帰結。空窓に対して日を列挙すると読む必要のない part を読む）。
    例外: なし。

    UTC 固定の根拠（ISSUE-402）: partition 列 year/month/day は
    ``with_partition_columns`` が naive UTC の保存 timestamp から生成する。列挙側も
    同じ UTC で日を数えなければ、プロセスのローカル TZ によって読む part が変わる。
    `datetime.fromtimestamp(epoch, tz=timezone.utc)` は ``time.tzname`` を参照しない。
    """
    if start_epoch >= end_epoch:
        return []

    start_day = _utc_day_start(start_epoch)
    # end の直前の秒が属する日まで列挙する（半開・粒度は epoch 秒）。
    end_day = _utc_day_start(end_epoch - 1)

    days: list[tuple[int, int, int]] = []
    cur = start_day
    while cur <= end_day:
        days.append((cur.year, cur.month, cur.day))
        cur += timedelta(days=1)
    return days


def _utc_day_start(epoch: int) -> datetime:
    """epoch 秒が属する UTC 日の 00:00:00（aware）。"""
    moment = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return datetime(moment.year, moment.month, moment.day, tzinfo=timezone.utc)
