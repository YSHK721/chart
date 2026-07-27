"""serve_candles（ISSUE-183 item6）— GET /candles・/forming_bar の業務手順。

adapter/controller/candles_controller が担っていた業務手順（datasetRef / timeframe のホワイトリスト
検証 → candles 取得 / 形成中バーの 3 段フォールバック → エラー翻訳）を純関数へ移設したもの。

経緯: DIP 適用が ``/compute`` のみに限定され、``/candles`` と ``/forming_bar`` は controller が
``marketdata.dataset`` を直呼びしていた（DIP 非対称）。本モジュールは HTTP の殻にも
marketdata / adapter の具象にも依存せず、Output Boundary（:class:`CandleDatasetPort`）と呼出時
注入された協調子（``forming_bar``）にのみ依存する（依存方向は外側 → 内側）。

責務境界:
  - Controller（外側）: クエリ文字列 → Input Model の変換（``limit`` / ``now`` の数値解釈）と、
    Output Model → (HTTP ステータス, ボディ) の翻訳（nested error 整形）。
  - 本モジュール（内側）: 検証・取得・フォールバックの手順と、error_type / message の決定。

error_type → HTTP ステータスの対応は presentation の責務であり、本層は error_type と message
のみを結果 DTO に載せる（HTTP 依存を内側へ持ち込まない）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from usecase.dataset_port import CandleDatasetPort, candle_dataset_port as _default_port


@dataclass
class CandlesRequest:
    """/candles の Input Model（プレーンデータ。HTTP のクエリ文字列は controller が解釈済み）。"""

    dataset_ref: Any = None
    timeframe: Any = None
    limit: "int | None" = None


@dataclass
class FormingBarRequest:
    """/forming_bar の Input Model（``now_override`` は controller が数値化済み・None は現在時刻）。"""

    dataset_ref: Any = None
    timeframe: Any = None
    now_override: "int | None" = None
    buffer: Any = None


@dataclass
class CandlesResult:
    """/candles の Output Model（成功は candles、失敗は error_type/message）。"""

    candles: Optional[list] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error_type is None


@dataclass
class FormingBarResult:
    """/forming_bar の Output Model（成功は bar＝``dict | None``、失敗は error_type/message）。

    ``bar is None`` は「更新なしの正常応答」であり失敗ではない（``ok`` は error_type で判定する）。
    """

    bar: Optional[dict] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error_type is None


def _validate_refs(port: Any, ref: Any, timeframe: Any) -> "tuple[str, str] | None":
    """datasetRef / timeframe のホワイトリスト検証（§7.3）。違反は (error_type, message)。

    timeframe が ``None`` のときは原子＝再集計なしとして検査対象外（後方互換）。
    """
    if not port.is_known(ref):
        return "validation", f"未知の datasetRef です: {ref!r}"
    if timeframe is not None and not port.is_known_timeframe(timeframe):
        return "validation", f"未知の timeframe です: {timeframe!r}"
    return None


def serve_candles(
    request: CandlesRequest,
    *,
    dataset_port: "Optional[CandleDatasetPort]" = None,
) -> CandlesResult:
    """ローソク配信（§6.3）の業務手順（純関数）。

    Args:
        request: Input Model（CandlesRequest）。
        dataset_port: CandleDatasetPort。None のとき既定ポートを解決する。

    Returns:
        CandlesResult（成功は candles、失敗は error_type/message）。
    """
    port = dataset_port if dataset_port is not None else _default_port()

    violation = _validate_refs(port, request.dataset_ref, request.timeframe)
    if violation is not None:
        return CandlesResult(error_type=violation[0], error_message=violation[1])

    try:
        candles = port.load_candles(request.dataset_ref, request.timeframe, request.limit)
    except Exception as exc:  # noqa: BLE001（業務手順の最後の砦・error 表現へ翻訳して返す）
        return CandlesResult(
            error_type="internal", error_message=f"candles 取得に失敗しました: {exc}"
        )
    return CandlesResult(candles=candles)


def _forming_bar_from_buffer(
    forming_bar: Any, ref: Any, timeframe: Any, now_unix: int, buffer: Any
) -> "dict | None":
    """parquet 経路が None のとき、in-memory tick バッファから現周期の形成中バーを組む（seed 鮮度化）。"""
    if buffer is None or not forming_bar.is_tick_ref(ref) \
            or not forming_bar.is_supported_timeframe(timeframe):
        return None
    start = forming_bar.period_start_unix(now_unix, timeframe)
    ticks = buffer.ticks_since(start * 1000 - 1)  # start 以降（境界含む）の (ms, mid)。
    return forming_bar.forming_bar_from_buffer_ticks(ticks, start, now_unix)


def serve_forming_bar(
    request: FormingBarRequest,
    *,
    dataset_port: "Optional[CandleDatasetPort]" = None,
    forming_bar: Any,
) -> FormingBarResult:
    """形成中バー（ライブ足内更新）の業務手順（純関数）。

    ロールアップ優先 → parquet → buffer の 3 段フォールバック。対象外 ref/tf・ティック無しは
    ``bar=None``（更新なしの正常応答）。

    Args:
        request: Input Model（FormingBarRequest）。
        dataset_port: CandleDatasetPort（ホワイトリスト検証にのみ使用）。None のとき既定を解決する。
        forming_bar: resolve_now_unix / rollup_forming_bar / forming_bar / is_tick_ref /
            is_supported_timeframe / period_start_unix / forming_bar_from_buffer_ticks を持つ
            協調子（呼出時注入）。

    Returns:
        FormingBarResult（成功は bar、失敗は error_type/message）。
    """
    port = dataset_port if dataset_port is not None else _default_port()

    violation = _validate_refs(port, request.dataset_ref, request.timeframe)
    if violation is not None:
        return FormingBarResult(error_type=violation[0], error_message=violation[1])

    ref, timeframe = request.dataset_ref, request.timeframe
    now_unix = forming_bar.resolve_now_unix(request.now_override)
    try:
        bar = forming_bar.rollup_forming_bar(ref, timeframe, now_unix, buffer=request.buffer)
        if bar is None:
            bar = forming_bar.forming_bar(ref, timeframe, now_unix)
            if bar is None:
                bar = _forming_bar_from_buffer(
                    forming_bar, ref, timeframe, now_unix, request.buffer
                )
    except Exception as exc:  # noqa: BLE001
        return FormingBarResult(
            error_type="internal", error_message=f"forming_bar 取得に失敗しました: {exc}"
        )
    return FormingBarResult(bar=bar)
