"""WindowedMarketDataRepository — 任意の `MarketDataPort` 実装へ取得窓を効かせるデコレータ。

新設理由（A-3・L-2 の解消）: 取得窓 `marketdata_window` は `CsvOHLCRepository` のときだけ
委譲 repo（`MarketDataSourceRepository`）へ差し替わり、`Mt5CsvOHLCRepository` では**無視**
されていた（実測: MA_Slope_EA + JP225 M1 2025-01 で窓あり／なしの bars が同一 sha256・
28097 本）。本モジュールは窓を「load の外側の合成」として適用し、この非対称を除去する。

無改変の範囲（A-3 の制約・実測で確認）:
    `MarketDataPort.load` の署名・各 repository（`ohlc_csv` / `ohlc_mt5_csv` /
    `tick_parquet` / `marketdata_source`）の実装・共通経路 `_ohlc_frame` は 1 行も
    変更していない。窓は本デコレータの**構築時パラメータ**だけが持つ。

SOLID 上の位置づけ:
    OCP  — 窓の適用は既存 repository を改変せず**追加**（合成）で実現する。新しい
           `MarketDataPort` 実装が増えても窓の適用側は改変不要（型で分岐しない）。
    LSP  — `MarketDataPort` として内側 port と置換可能。`load` の 3 引数はそのまま内側へ
           渡す（事前条件を強化しない）。返り値は同一 `Bar` の部分列であり、時刻昇順という
           事後条件を保存する（絞るだけで並べ替え・写像をしない）。
    SRP  — 責務は「窓で絞ること」ひとつ。読み取り（I/O・列マッピング・例外翻訳）は内側
           port が持ち続ける。
    DIP  — 具象 repository ではなく `MarketDataPort` 抽象に依存する。

委譲経路へ寄せない根拠（実測・H-4）:
    `MarketDataSourceRepository._candles_to_bars` は `spread=0` 固定である
    （`marketdata_source.py:51`）。MT5 経路をそこへ寄せると spread 依存戦略
    （MA_Slope / MA_Slope_Pending / StopEntryProbe）の約定価格式（買い = open + spread×point）
    が壊れる。本デコレータは Bar を**同一インスタンスのまま**通すため spread を保存する。

半開区間（既存規約に一致）:
    `[start, end)`。`marketdata/csv_source.py` の `if t < start_ts or t >= end_ts: continue`
    と同一。境界は UTC aware datetime（`main/tester_settings/window.py resolve_data_window`
    が生成する）。`bar.time` は経路により epoch int / `numpy.datetime64` に分かれるため、
    比較前に `simulator.domain.bar_time.epoch_seconds` で正規化する（正規化の実体は domain が
    単一ソースとして所有し、本モジュールは書き直さない）。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.bar import Bar
from simulator.domain.bar_time import epoch_seconds
from simulator.usecase.ports import MarketDataPort


class WindowedMarketDataRepository(MarketDataPort):
    """内側 `MarketDataPort` へ委譲し、構築時窓 `[start, end)` で bars を絞る。"""

    def __init__(self, inner: MarketDataPort, *, window: Any) -> None:
        # DI: 内側 port と取得窓を構築時に注入する。取得窓は本実装固有の選択軸のため
        # 構築時へ隔離し、`load` の `source_ref` は path 系実装と対称のまま保つ
        # （ISSUE-135 の LSP 規律を踏襲）。``window`` が ``None`` のときは素通しする。
        self._inner = inner
        self._window = window  # (start, end)（半開）または None

    @property
    def inner(self) -> MarketDataPort:
        """包んでいる内側 port（読み取り専用）。

        公開する理由（現存要求）: 「窓を課しても spread=0 の委譲経路へ紛れ込まない」
        （H-4）という不変条件は、合成後は**内側が何か**を見ないと測れない。
        `test_composition_marketdata_delegation` / `test_marketdata_window_mt5_path` が
        この不変条件を固定するために読む。差し替えは提供しない（構築後は不変）。
        """
        return self._inner

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> "list[Bar]":
        """内側 port の bars を構築時窓 `[start, end)` で絞って返す。

        事前条件: 内側 port の `load` 事前条件と同一（`source_ref` はパス様の参照）。
            引数は改変せずそのまま委譲する。
        事後条件: 返り値は内側 bars の**部分列**であり、要素は同一 `Bar` インスタンス
            （spread を含む全フィールドが無改変）。時刻昇順は内側の事後条件を保存する。
            窓が ``None`` のときは内側の返り値をそのまま返す。
        例外: 内側 port の例外契約（`DataError` / `TimeOrderError` / `OHLCInvalidError`）を
            そのまま伝播する。`bar.time` が未対応の時刻表現なら `ConfigError`
            （`epoch_seconds` の契約・推測で解釈しない）。
        """
        bars = self._inner.load(source_ref, timeframe, period)
        if self._window is None:
            return bars
        start, end = self._window
        start_epoch = epoch_seconds(start)
        end_epoch = epoch_seconds(end)
        # 半開 `[start, end)`（`marketdata/csv_source.py` の判定と同一規約）。
        return [bar for bar in bars if start_epoch <= epoch_seconds(bar.time) < end_epoch]
