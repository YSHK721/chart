"""OHLC CSV ローダ共通ヘルパー（adapter 内部・DataFrame→domain.Bar 変換）。

comma 形式（``ohlc_csv``）と MT5 タブ形式（``ohlc_mt5_csv``）の共通部
（必須列チェック・行ループ・``domain.Bar`` 生成・時刻昇順チェック・例外翻訳）を
1 箇所へ集約する。形式差（pandas 読み込み引数・列名・時刻パース）は各実装が
``ColumnSpec`` で注入する（CLEAN_ARCH §6 外側例外の内側翻訳は本ヘルパーに集約）。

adapter 層内部ヘルパー（usecase/domain にのみ依存・pandas を技術ドライバとして使用）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from simulator.domain.bar import Bar
from simulator.domain.exceptions import DataError, MissingBarError, TimeOrderError


@dataclass(frozen=True)
class ColumnSpec:
    """1 形式分の列マッピング（形式差のみを保持する）。

    required: 必須列名タプル（欠損時 MissingBarError）。
    extract: ``(df, i)`` を受け、位置 ``i`` の 1 行を ``domain.Bar`` 引数 dict へ
        変換する抽出関数（実装は ``df["col"].iat[i]`` で位置参照する）。形式ごとの
        列名・時刻パースの差をここへ閉じる。
    """

    required: tuple[str, ...]
    extract: Callable[[pd.DataFrame, int], "dict[str, Any]"]


def read_csv_or_data_error(source_ref: Any, *, sep: str | None = None) -> pd.DataFrame:
    """pandas で CSV を読み、外側例外を内側 DataError へ翻訳する（漏出禁止）。"""
    try:
        return pd.read_csv(source_ref) if sep is None else pd.read_csv(source_ref, sep=sep)
    except Exception as exc:  # pandas / OSError 等を内側へ翻訳
        raise DataError(
            f"CSV の読み込みに失敗しました: {source_ref}",
            context={"source_ref": str(source_ref), "cause": repr(exc)},
        ) from exc


def frame_to_bars(df: pd.DataFrame, spec: ColumnSpec) -> list[Bar]:
    """DataFrame を検証して domain.Bar 列へ変換する（全形式共通の制御フロー）。

    必須列欠損 → MissingBarError / OHLC 整合違反 → domain.Bar が OHLCInvalidError /
    時刻昇順違反 → TimeOrderError（CLEAN_ARCH §6）。形式差は spec.extract に閉じる。
    """
    missing = [c for c in spec.required if c not in df.columns]
    if missing:
        raise MissingBarError(
            f"必須列が不足しています: {missing}",
            context={"missing": missing, "columns": list(df.columns)},
        )

    bars: list[Bar] = []
    prev_time = None
    for i in range(len(df)):
        # OHLC 整合違反は domain.Bar が OHLCInvalidError を送出（内側例外・翻訳不要）
        bar = Bar(**spec.extract(df, i))
        if prev_time is not None and bar.time <= prev_time:
            raise TimeOrderError(
                "時刻が昇順ではありません",
                bar_index=i,
                context={"prev_time": str(prev_time), "time": str(bar.time)},
            )
        prev_time = bar.time
        bars.append(bar)

    return bars
