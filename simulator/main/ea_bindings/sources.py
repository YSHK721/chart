"""EA 束縛が共有するデータ供給（CSV 読み・OHLC リーダの選択）。ISSUE-502 段階 4A。

複数の EA が同じ読み方をするため、読み方そのものはここが唯一の所有者である
（EA モジュールごとに写すと、列名の正規化が片方だけ改訂される）。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository
from simulator.adapter.repository.ohlc_marketdata_csv import (
    MarketdataCsvOHLCRepository,
    detect_ohlc_form,
)
from simulator.domain.exceptions import DataError


def load_dataframe(data_path: Any) -> pd.DataFrame:
    """指標 registry の事前計算用に価格 CSV を DataFrame として読み込む。

    registry は系列（pandas）を要するため DataFrame が必須。一方 Interactor は Bar 列を
    消費し、controller.run は committed IF（source_ref パス）上で再度 load する。registry
    の DataFrame 需要・Interactor の Bar 需要・controller の path 再読みを 1 回の読み込みへ
    統合するには committed adapter/usecase の IF 変更が要るため範囲外＝申し送り。
    外側（pandas/OS）例外は内側 DataError へ翻訳し漏出を防ぐ（CLEAN_ARCH §6）。
    """
    try:
        return pd.read_csv(data_path)
    except Exception as exc:
        raise DataError(
            f"指標計算用 CSV の読み込みに失敗しました: {data_path}",
            context={"data_path": str(data_path), "cause": repr(exc)},
        ) from exc


def load_mt5_dataframe(data_path: Any) -> pd.DataFrame:
    """MT5 エクスポート形式（タブ区切り）を指標 registry 用 DataFrame として読み込む。

    `<CLOSE>` 等の MT5 列名を registry/EMA 計算が参照する小文字列名（close 等）へ
    正規化する（MA_Slope 系の registry は df["close"] を参照）。外側例外は内側
    DataError へ翻訳する（CLEAN_ARCH §6）。
    """
    try:
        df = pd.read_csv(data_path, sep="\t")
    except Exception as exc:
        raise DataError(
            f"指標計算用 MT5 CSV の読み込みに失敗しました: {data_path}",
            context={"data_path": str(data_path), "cause": repr(exc)},
        ) from exc
    return df.rename(
        columns={
            "<OPEN>": "open",
            "<HIGH>": "high",
            "<LOW>": "low",
            "<CLOSE>": "close",
        }
    )


def ohlc_repository_for(data_path: Any) -> Any:
    """spread 非依存（comma 系）EA の OHLC リーダをデータ実体の形式で選ぶ。

    形式の権威はデータのヘッダ（`detect_ohlc_form`）である。marketdata 形式
    （2012 年からの全期間 JP225 実データ・依頼者承認 2026-09-06）は
    `MarketdataCsvOHLCRepository`、それ以外は従来どおり `CsvOHLCRepository`
    （comma 合成データの既存経路と byte 等価）。spread 依存 EA（MT5 ローダ）には
    使わない——marketdata 形式は spread を持たず、その組合せは N-17 が実行前に弾く。
    """
    if detect_ohlc_form(data_path) == "marketdata":
        return MarketdataCsvOHLCRepository()
    return CsvOHLCRepository()
