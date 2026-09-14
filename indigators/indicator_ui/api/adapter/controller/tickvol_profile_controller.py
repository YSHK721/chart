"""GET /tickvol_profile の薄殻 controller（Controller + Presenter）。

:mod:`adapter.controller.candles_controller` と同型。業務手順は usecase
（:mod:`usecase.serve_tickvol_profile`）が持ち、本 module は次の 2 責務のみを担う:
  - Controller: クエリ由来の生値（sessions / pct / until の文字列）を Input Model へ変換する。
  - Presenter : Output Model を (HTTP ステータス, レスポンスボディ) へ翻訳する。

集計協調子（marketdata.tickvol_profile）は本 module の名前解決を通して注入する
（``candles_controller`` の ``forming_bar_mod`` と同規律＝テストの monkeypatch アンカーを残す）。
リプレイ core（simulator/replay_ui）は api_loader 経由で本 handler を read-only 再利用
するため、実装は 1 か所のままライブ・リプレイ双方の応答が byte 一致する。
"""
from __future__ import annotations

from typing import Any

from adapter.compute import tickvol_profile as tickvol_profile_mod
from usecase.serve_tickvol_profile import (
    TickvolProfileRequest,
    TickvolProfileResult,
    serve_tickvol_profile,
)


def _int_or_none(raw: Any) -> "int | None":
    """クエリ生値を int へ（空・非数は None＝既定へ委ねる）。負号も受ける（until 用）。"""
    s = str(raw) if raw is not None else ""
    return int(s) if s.lstrip("-").isdigit() else None


def _present(result: TickvolProfileResult) -> "tuple[int, dict]":
    """Output Model を (HTTP ステータス, ボディ) へ翻訳する（Presenter）。"""
    if not result.ok:
        from api_shared.http_contract import nested_error

        return nested_error(result.error_type, result.error_message)
    return 200, {
        "ok": True,
        "binSec": result.bin_sec,
        "sessions": result.sessions,
        "bands": result.bands,
        "bins": result.bins,
        "threshold": result.threshold,
    }


def handle_tickvol_profile(
    ref: Any, sessions_raw: Any = None, pct_raw: Any = None, until_raw: Any = None
) -> "tuple[int, dict]":
    """取引密度プロファイル配信: datasetRef を whitelist 検証し HIGH 帯とビン代表値を返す。

    ``until`` は因果カットオフ（UNIX 秒）。省略時はデータ末尾まで＝ライブの現在。``until`` が属する
    セッション日は集計に含めない（リプレイで当日の未来バーを覗かないための絶対条件）。
    """
    return _present(
        serve_tickvol_profile(
            TickvolProfileRequest(
                dataset_ref=ref,
                sessions=_int_or_none(sessions_raw),
                pct=_int_or_none(pct_raw),
                until=_int_or_none(until_raw),
            ),
            profile=tickvol_profile_mod,
        )
    )
