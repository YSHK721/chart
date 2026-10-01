"""OHLC CSV ローダ共通ヘルパー（adapter 内部・DataFrame→domain.Bar 変換）。

comma 形式（``ohlc_csv``）と MT5 タブ形式（``ohlc_mt5_csv``）の共通部
（必須列チェック・行ループ・``domain.Bar`` 生成・時刻昇順チェック・例外翻訳）を
1 箇所へ集約する。形式差（pandas 読み込み引数・列名・時刻パース）は各実装が
``ColumnSpec`` で注入する（CLEAN_ARCH §6 外側例外の内側翻訳は本ヘルパーに集約）。

adapter 層内部ヘルパー（usecase/domain にのみ依存・pandas を技術ドライバとして使用）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

import pandas as pd

from simulator.adapter.repository import ohlc_frame_cache
from simulator.domain.bar import Bar
from simulator.domain.exceptions import DataError, MissingBarError, TimeOrderError


@dataclass(frozen=True)
class ColumnSpec:
    """1 形式分の列マッピング（形式差のみを保持する）。

    required: 必須列名タプル（欠損時 MissingBarError）。
    columns: ``df`` を受け、``domain.Bar`` の引数名 → 全行ぶんの値の列（行の並びのまま）を
        返す関数。形式ごとの列名・時刻パースの差をここへ閉じる。列は**列ごとに 1 回だけ**
        取り出す（ISSUE-551 段 2: 1 行ごとに ``df["col"].iat[i]`` で取り出すと、取り出しが
        行数 × 列数回発行され 215 万行で 3〜4 分かかった）。
    """

    required: tuple[str, ...]
    columns: Callable[[pd.DataFrame], "dict[str, Sequence[Any]]"]


#: 変換の観測口（検定の注入点・絶対命令 2026-09-25「観測の境界を宣言する」）。
#: `frame_to_bars` が 1 回ごとに (Bar にした行数, その形式が宣言した必須列) を渡す。既定なし。
_observer: "Callable[[int, tuple[str, ...]], None] | None" = None


def set_observer(observer: "Callable[[int, tuple[str, ...]], None] | None") -> None:
    """変換の観測口を差し替える（``None`` で外す）。検定の注入点。"""
    global _observer
    _observer = observer


def cell_values(column: pd.Series) -> "Sequence[Any]":
    """列の全行を、従来の行ごとの ``column.iat[i]`` と**同じ値・同じ型**の列にする。

    datetime64 の列だけは ``iat`` が pandas.Timestamp へ包むので同じく包む（包まずに
    numpy.datetime64 を返すと、`domain.Bar` が拒む表現（Timestamp）の行が受理へ変わる）。
    """
    if pd.api.types.is_datetime64_any_dtype(column.dtype):
        return list(column)
    return column.to_numpy()


def float_values(column: pd.Series) -> "list[float]":
    """列の全行を Python の float の列にする（従来の行ごとの ``float(値)`` と同じ値）。"""
    return column.to_numpy(dtype=float).tolist()


def int_values(column: pd.Series) -> "list[int]":
    """列の全行を Python の int の列にする（従来の行ごとの ``int(値)`` と同じ規則）。"""
    return [int(value) for value in column.tolist()]


def read_csv_or_data_error(source_ref: Any, *, sep: str | None = None) -> pd.DataFrame:
    """読みの単一点（`ohlc_frame_cache`）から読み、外側例外を内側 DataError へ翻訳する。

    parse は 1 プロセス 1 実体 1 回（ISSUE-541 段 2）。返る DataFrame は共有実体であり
    **読むだけ**にする（`frame_to_bars` は読むだけ・各リーダの後段も同様）。
    """
    try:
        return ohlc_frame_cache.read_frame(source_ref, sep=sep)
    except Exception as exc:  # pandas / OSError 等を内側へ翻訳
        raise DataError(
            f"CSV の読み込みに失敗しました: {source_ref}",
            context={"source_ref": str(source_ref), "cause": repr(exc)},
        ) from exc


def frame_to_bars(df: pd.DataFrame, spec: ColumnSpec) -> list[Bar]:
    """DataFrame を検証して domain.Bar 列へ変換する（全形式共通の制御フロー）。

    必須列欠損 → MissingBarError / OHLC 整合違反 → domain.Bar が OHLCInvalidError /
    時刻昇順違反 → TimeOrderError（CLEAN_ARCH §6）。形式差は spec.columns に閉じる。
    """
    missing = [c for c in spec.required if c not in df.columns]
    if missing:
        raise MissingBarError(
            f"必須列が不足しています: {missing}",
            context={"missing": missing, "columns": list(df.columns)},
        )

    if _observer is not None:
        _observer(len(df), spec.required)
    fields = spec.columns(df)
    names = tuple(fields)
    bars: list[Bar] = []
    prev_time = None
    for i, values in enumerate(zip(*fields.values())):
        # OHLC 整合違反は domain.Bar が OHLCInvalidError を送出（内側例外・翻訳不要）
        bar = Bar(**dict(zip(names, values)))
        if prev_time is not None and bar.time <= prev_time:
            raise TimeOrderError(
                "時刻が昇順ではありません",
                bar_index=i,
                context={"prev_time": str(prev_time), "time": str(bar.time)},
            )
        prev_time = bar.time
        bars.append(bar)

    return bars
