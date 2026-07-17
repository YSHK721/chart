"""GET /candles・/forming_bar の純ロジック controller（HTTP 殻非依存・ISSUE-087 🟡-1）。

旧状態: framework/server.py の殻メソッドが dataset/forming_bar を直接呼び、検証・分岐
（ロールアップ優先→parquet→buffer フォールバック）が殻へ漏出していた。/compute・
/market_profile 系（handle_x 純関数）と同型の (status, body) 関数へ抽出し、殻は
「クエリ取り出し→handle→JSON 送出」のみへ縮小する（層飛び越しの解消）。
"""
from __future__ import annotations

from typing import Any

from adapter.compute import dataset, forming_bar as forming_bar_mod


def _error(error_type: str, message: str) -> "tuple[int, dict]":
    # エラーボディ整形は nested_error（api_shared・単一定義）へ委譲し、正典形との暗黙同期を
    #   解消する（ISSUE-104 🟡-2）。従来は violations 欠落＋series:[] 追加で正典と乖離していた。
    #   candles 固有の series:[]（系列消費側の非破壊フォールバック）は基底へ合成して温存する。
    from api_shared.http_contract import nested_error

    status, body = nested_error(error_type, message)
    body["series"] = []
    return status, body


def handle_candles(ref: Any, timeframe: Any, limit_raw: Any) -> "tuple[int, dict]":
    """ローソク配信（§6.3）: datasetRef/timeframe を whitelist 検証し candles を返す。"""
    if not dataset.is_known(ref):
        return _error("validation", f"未知の datasetRef です: {ref!r}")
    if timeframe is not None and not dataset.is_known_timeframe(timeframe):
        return _error("validation", f"未知の timeframe です: {timeframe!r}")
    limit = int(limit_raw) if (limit_raw and str(limit_raw).isdigit()) else None
    try:
        candles = dataset.load_candles(ref, timeframe, limit)
    except Exception as exc:  # noqa: BLE001（controller の最後の砦・nested で返す）
        return _error("internal", f"candles 取得に失敗しました: {exc}")
    return 200, {"ok": True, "candles": candles}


def _forming_bar_from_buffer(ref: Any, timeframe: Any, now_unix: int, buffer: Any) -> "dict | None":
    """parquet 経路が None のとき、in-memory LiveTickBuffer から現周期の形成中バーを組む（seed 鮮度化）。"""
    if buffer is None or not forming_bar_mod.is_tick_ref(ref) \
            or not forming_bar_mod.is_supported_timeframe(timeframe):
        return None
    start = forming_bar_mod.period_start_unix(now_unix, timeframe)
    ticks = buffer.ticks_since(start * 1000 - 1)  # start 以降（境界含む）の (ms, mid)。
    return forming_bar_mod.forming_bar_from_buffer_ticks(ticks, start, now_unix)


def handle_forming_bar(ref: Any, timeframe: Any, now_raw: Any, buffer: Any = None) -> "tuple[int, dict]":
    """形成中バー（ライブ足内更新）: ロールアップ優先 → parquet → buffer の 3 段フォールバック。

    ``{ok: True, bar: {...} | null}``。対象外 ref/tf・ティック無しは bar=null（更新なしの正常応答）。
    """
    if not dataset.is_known(ref):
        return _error("validation", f"未知の datasetRef です: {ref!r}")
    if timeframe is not None and not dataset.is_known_timeframe(timeframe):
        return _error("validation", f"未知の timeframe です: {timeframe!r}")
    now_override = int(now_raw) if (now_raw and str(now_raw).lstrip("-").isdigit()) else None
    now_unix = forming_bar_mod.resolve_now_unix(now_override)
    try:
        bar = forming_bar_mod.rollup_forming_bar(ref, timeframe, now_unix, buffer=buffer)
        if bar is None:
            bar = forming_bar_mod.forming_bar(ref, timeframe, now_unix)
            if bar is None:
                bar = _forming_bar_from_buffer(ref, timeframe, now_unix, buffer)
    except Exception as exc:  # noqa: BLE001
        return _error("internal", f"forming_bar 取得に失敗しました: {exc}")
    return 200, {"ok": True, "bar": bar}
