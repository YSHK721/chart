"""CsvCandleSource — comma 形式 CSV を :class:`CandleSource` ポートへ適合させる adapter。

simulator の comma 形式 OHLC CSV（``time,open,high,low,close,volume[,spread]``）の実体移管先
（設計 §1.1・§3.3）。``MarketDataSourceRepository`` が本 source へ委譲し ``Candle → domain.Bar``
を写像する（委譲経路）。

Candle 契約（§2.1）に従い ``time`` は UNIX 秒 int、``volume`` は列があれば float／無ければ
``0.0``。``fetch_candles(start, end)`` は ``[start, end)``（半開・C-2）で期間フィルタする。

窓境界の正規化と半開判定は**自前で書かない**（ISSUE-401 🟡-2 の是正）:
    以前は ``int(start.timestamp())`` を本モジュールが直接持っていたため、naive datetime を
    **プロセスのローカル TZ**で解釈していた。同じ窓を受け取る Bar 段
    （``simulator/adapter/repository/windowed_market_data.py``）は naive を **UTC** とみなす
    ため、解釈が経路で食い違っていた（実測: ``TZ=Asia/Tokyo``・naive ``datetime(2025, 1, 10)``
    で 32400 秒＝9 時間差。同じ窓指定で選択される足が変わり、バックテストが実行環境に依存した）。
    規則の実体は中立共有パッケージ ``datawindow.half_open`` が唯一所有し、本モジュールと Bar 段
    が**同じオブジェクト**を読む。`marketdata` は独立パッケージであり `simulator` を import
    できない（依存方向）ため、共有点は両パッケージの外側にある。

pandas はインフラ境界の技術ドライバとして adapter 内に隔離する（ポート面には出さない）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List

import pandas as pd

from datawindow.half_open import HalfOpenEpochWindow
from marketdata.port import Candle


class CsvCandleSource:
    """comma 形式 CSV から OHLC candles を取得する :class:`CandleSource` 実装。

    CSV パスは構築時に固定し、:meth:`fetch_candles` は期間のみを受ける（ポートの呼び出し面に
    ファイルパスを出さない＝CandleSource は datetime のみ受ける契約）。
    """

    def __init__(self, csv_path: Any) -> None:
        self._csv_path = csv_path

    def fetch_candles(self, start: datetime, end: datetime) -> List[Candle]:
        """``[start, end)`` の candles を time 昇順・一意で返す（データなしは空 list・半開）。

        CandleSource 契約（``marketdata/port.py``・ISSUE-098 🟡-3）: 返す candles は ``time``
        厳密昇順・一意。同一 ``time`` は**後勝ち**で一意化する（Dukascopy 実装 ``_to_candles``
        と対称）。実データ（実 OHLC CSV）は time 一意のため後勝ち一意化は no-op（byte 不変）。
        """
        df = pd.read_csv(self._csv_path)
        has_volume = "volume" in df.columns
        # 境界正規化（naive は UTC とみなす）も半開判定も共有実体が持つ（複製を作らない）。
        window = HalfOpenEpochWindow.from_datetimes(start, end)

        # 後勝ち一意化: ``time`` をキーに dict 格納（Dukascopy ``_to_candles`` と同一構造）。
        by_time: dict[int, Candle] = {}
        for i in range(len(df)):
            raw_t = df["time"].iat[i]
            try:
                t = int(raw_t)
            except (TypeError, ValueError) as exc:
                # Candle 契約（§2.1）: time は UNIX 秒 int。ISO 文字列等の非 epoch を委譲経路へ
                # 流すと黙って report.json が乖離するため fail-fast で契約を明示する（暗黙の
                # フォールバックを設けない・dukascopy_source の KeyError 同方針）。
                raise ValueError(
                    "CsvCandleSource: 'time' 列は UNIX 秒 int である必要があります"
                    f"（非 epoch 値を検出: {raw_t!r}・row={i}）。Candle 契約 §2.1。"
                ) from exc
            if not window.contains(t):  # [start, end) 半開（C-2・述語も単一ソース）
                continue
            # Candle 契約（port.py Candle.volume・ISSUE-102 🟡-1）: volume は常に有限 float。
            #   欠損（列不在／セル NaN）は 0.0 で補う（Dukascopy _to_candles:88 `pd.isna→0.0`
            #   と対称）。列不在のみ 0.0・セル NaN 未ガードだと NaN が下流へ伝播し、契約に依存する
            #   利用側を CSV 実装へ差し替えると二重計上/NaN 汚染を起こす（LSP 非対称）。実データ
            #   （実 OHLC CSV 34 ファイル）は volume NaN 0 件のため本ガードは no-op（byte 不変）。
            raw_v = df["volume"].iat[i] if has_volume else None
            v = 0.0 if (raw_v is None or pd.isna(raw_v)) else float(raw_v)
            by_time[t] = {
                "time": t,
                "open": float(df["open"].iat[i]),
                "high": float(df["high"].iat[i]),
                "low": float(df["low"].iat[i]),
                "close": float(df["close"].iat[i]),
                "volume": v,
            }
        return [by_time[t] for t in sorted(by_time)]
