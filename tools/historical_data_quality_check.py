from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import dukascopy_python
from dukascopy_python.instruments import INSTRUMENT_IDX_ASIA_E_N225JAP


REQUIRED_COLUMNS = ["日付", "時間", "始値", "高値", "安値", "終値", "出来高"]


def _load_history(csv_path: str | Path) -> pd.DataFrame:
    path = Path(csv_path)
    df = pd.read_csv(path, dtype={"日付": "string", "時間": "string"})
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")
    return df


def _normalize_timestamp(row: pd.Series) -> str:
    return f"{row['日付']} {row['時間']}"


def check_historical_csv(csv_path: str | Path) -> dict[str, Any]:
    df = _load_history(csv_path)
    issues: list[str] = []
    duplicates = df.duplicated(subset=["日付", "時間"], keep=False)
    if duplicates.any():
        issues.append("duplicate_timestamp")

    ohlc = (
        (df["始値"] <= df["高値"]) &
        (df["始値"] >= df["安値"]) &
        (df["終値"] <= df["高値"]) &
        (df["終値"] >= df["安値"]) &
        (df["高値"] >= df["安値"])
    )
    if not ohlc.all():
        issues.append("ohlc_violation")

    return {
        "valid": not issues,
        "issues": issues,
        "rows": int(len(df)),
        "duplicate_rows": int(duplicates.sum()),
        "invalid_ohlc_rows": int((~ohlc).sum()),
    }


def compare_close_series(left_csv: str | Path, right_csv: str | Path) -> dict[str, Any]:
    left = _load_history(left_csv)
    right = _load_history(right_csv)

    left_key = left[["日付", "時間"]].copy()
    right_key = right[["日付", "時間"]].copy()
    left_key["ts"] = left_key.apply(_normalize_timestamp, axis=1)
    right_key["ts"] = right_key.apply(_normalize_timestamp, axis=1)

    left_set = left_key.set_index("ts")
    right_set = right_key.set_index("ts")

    merged = left_set.join(right_set, how="inner", lsuffix="_left", rsuffix="_right")
    matched = merged.index.tolist()
    left_close = left.set_index(left_key["ts"])["終値"].reindex(matched)
    right_close = right.set_index(right_key["ts"])["終値"].reindex(matched)

    corr = float(left_close.corr(right_close)) if len(left_close) > 1 else 1.0
    result = {
        "matched_rows": int(len(matched)),
        "correlation": corr,
        "summary": {
            "matched_rows": int(len(matched)),
            "correlation": corr,
            "left_csv": str(Path(left_csv)),
            "right_csv": str(Path(right_csv)),
        },
    }
    result["summary_json"] = json.dumps(result["summary"], ensure_ascii=False, sort_keys=True)
    return result


def compare_csv_to_dukascopy(csv_path: str | Path, *, instrument: str = INSTRUMENT_IDX_ASIA_E_N225JAP) -> dict[str, Any]:
    """ローカルの JP225 1分足 CSV を Dukascopy 1分足と JST→UTC で比較する。

    元データは絶対に書き換えない。row 単位の読み取りだけを行い、比較対象は Dukascopy の
    取得結果だけを使う。"""
    df_local = _load_history(csv_path)
    local_ts = pd.to_datetime(
        df_local["日付"] + " " + df_local["時間"],
        format="%Y/%m/%d %H:%M",
        errors="coerce",
        utc=False,
    )
    if local_ts.isna().any():
        raise ValueError("local CSV contains invalid timestamps")
    local_df = df_local.copy()
    local_df["dt_jst"] = local_ts.dt.tz_localize("Asia/Tokyo")
    local_df["dt_utc"] = local_df["dt_jst"].dt.tz_convert("UTC")

    start = local_df["dt_utc"].min().to_pydatetime()
    end = (local_df["dt_utc"].max() + pd.Timedelta(minutes=1)).to_pydatetime()

    dukascopy_df = dukascopy_python.fetch(
        instrument,
        dukascopy_python.INTERVAL_MIN_1,
        dukascopy_python.OFFER_SIDE_BID,
        start,
        end,
    )
    if dukascopy_df is None or dukascopy_df.empty:
        return {
            "matched_rows": 0,
            "correlation": float("nan"),
            "summary": {"matched_rows": 0, "correlation": None, "source": "dukascopy"},
        }

    duka = dukascopy_df.reset_index().copy()
    if not {"open", "high", "low", "close", "volume"}.issubset(set(duka.columns)):
        raise ValueError("Dukascopy data is missing OHLCV columns")
    duka.columns = ["dt_utc", "open", "high", "low", "close", "volume"]
    duka["dt_utc"] = pd.to_datetime(duka["dt_utc"], utc=True)
    duka["dt_jst"] = duka["dt_utc"].dt.tz_convert("Asia/Tokyo")

    merged = local_df[["dt_jst", "終値"]].rename(columns={"終値": "local_close"}).merge(
        duka[["dt_jst", "close"]].rename(columns={"close": "duka_close"}),
        on="dt_jst",
        how="inner",
    )

    if len(merged) <= 1:
        corr = 1.0 if len(merged) == 1 else float("nan")
    else:
        corr = float(merged["local_close"].corr(merged["duka_close"]))

    result = {
        "matched_rows": int(len(merged)),
        "correlation": corr,
        "summary": {
            "matched_rows": int(len(merged)),
            "correlation": corr,
            "left_csv": str(Path(csv_path)),
            "source": "dukascopy",
            "instrument": str(instrument),
        },
    }
    result["summary_json"] = json.dumps(result["summary"], ensure_ascii=False, sort_keys=True)
    return result


def compare_csv_to_dukascopy_by_hour(
    csv_path: str | Path,
    *,
    instrument: str = INSTRUMENT_IDX_ASIA_E_N225JAP,
) -> dict[str, Any]:
    """ローカル CSV と Dukascopy を JST 上で 1 時間ごとの相関に落とし込む。

    返却値の `hours` には、各時間帯の `hour` と `correlation` が並ぶ。
    1 時間あたり 2 件以上のマッチした分足があれば相関を計算し、
    それ以下なら `NaN` とする。"""
    df_local = _load_history(csv_path)
    local_ts = pd.to_datetime(
        df_local["日付"] + " " + df_local["時間"],
        format="%Y/%m/%d %H:%M",
        errors="coerce",
        utc=False,
    )
    if local_ts.isna().any():
        raise ValueError("local CSV contains invalid timestamps")

    local_df = df_local.copy()
    local_df["dt_jst"] = local_ts.dt.tz_localize("Asia/Tokyo")
    local_df["dt_utc"] = local_df["dt_jst"].dt.tz_convert("UTC")

    start = local_df["dt_utc"].min().to_pydatetime()
    end = (local_df["dt_utc"].max() + pd.Timedelta(minutes=1)).to_pydatetime()

    dukascopy_df = dukascopy_python.fetch(
        instrument,
        dukascopy_python.INTERVAL_MIN_1,
        dukascopy_python.OFFER_SIDE_BID,
        start,
        end,
    )
    if dukascopy_df is None or dukascopy_df.empty:
        return {
            "hours": [],
            "summary": {
                "left_csv": str(Path(csv_path)),
                "source": "dukascopy",
                "instrument": str(instrument),
                "hourly_count": 0,
            },
        }

    duka = dukascopy_df.reset_index().copy()
    if not {"open", "high", "low", "close", "volume"}.issubset(set(duka.columns)):
        raise ValueError("Dukascopy data is missing OHLCV columns")
    duka.columns = ["dt_utc", "open", "high", "low", "close", "volume"]
    duka["dt_utc"] = pd.to_datetime(duka["dt_utc"], utc=True)
    duka["dt_jst"] = duka["dt_utc"].dt.tz_convert("Asia/Tokyo")

    merged = local_df[["dt_jst", "終値"]].rename(columns={"終値": "local_close"}).merge(
        duka[["dt_jst", "close"]].rename(columns={"close": "duka_close"}),
        on="dt_jst",
        how="inner",
    )
    if merged.empty:
        return {
            "hours": [],
            "summary": {
                "left_csv": str(Path(csv_path)),
                "source": "dukascopy",
                "instrument": str(instrument),
                "hourly_count": 0,
            },
        }

    merged["hour"] = merged["dt_jst"].dt.strftime("%H")

    hour_rows: list[dict[str, Any]] = []
    for hour, group in merged.groupby("hour", sort=True):
        if len(group) <= 1:
            corr = float("nan")
        else:
            corr = float(group["local_close"].corr(group["duka_close"]))
        hour_rows.append(
            {
                "hour": str(hour),
                "matched_rows": int(len(group)),
                "correlation": corr,
            }
        )

    result = {
        "hours": hour_rows,
        "summary": {
            "left_csv": str(Path(csv_path)),
            "source": "dukascopy",
            "instrument": str(instrument),
            "hourly_count": len(hour_rows),
            "hourly_correlations": hour_rows,
        },
    }
    result["summary_json"] = json.dumps(result["summary"], ensure_ascii=False, sort_keys=True)
    return result
