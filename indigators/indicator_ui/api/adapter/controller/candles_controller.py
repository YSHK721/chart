"""GET /candles・/forming_bar の薄殻 controller（Controller + Presenter・ISSUE-087 🟡-1 / ISSUE-183 item6）。

ISSUE-087 🟡-1: framework/server.py の殻メソッドが dataset/forming_bar を直接呼び、検証・分岐
（ロールアップ優先→parquet→buffer フォールバック）が殻へ漏出していたものを (status, body) 関数へ抽出した。

ISSUE-183 item6: さらに業務手順（datasetRef/timeframe 検証 → 取得 → 3 段フォールバック → エラー翻訳）を
usecase 純関数 :mod:`usecase.serve_candles` へ移設し、``marketdata.dataset`` 直呼びを usecase 所有の
Output Boundary（``CandleDatasetPort``）経由へ統一した。従来は DIP 適用が ``/compute`` のみに限定され、
``/candles``・``/forming_bar`` だけが具象直結という非対称が残っていた。

本 controller は次の 2 責務のみを担う:
  - Controller: クエリ由来の生値（``limit`` / ``now`` の文字列）を Input Model へ変換して usecase を呼ぶ。
  - Presenter: Output Model を (HTTPステータス, レスポンスボディ) へ翻訳する。

協調子（forming_bar）は本 module の名前解決を通す（呼出時に module グローバルを参照）。これにより
既存テストの monkeypatch 経路（``cc.forming_bar_mod.*`` / ``cc.dataset.load_candles``）は不変のまま温存される。
"""
from __future__ import annotations

from typing import Any

# ``adapter.compute.dataset`` は ``marketdata.dataset`` 本体と同一モジュールオブジェクト（compute/dataset.py の
#   sys.modules 差し替え）。取得経路は既定 gateway（CandleDatasetPort 実装）へ移ったが、本 import は
#   既存テストの monkeypatch アンカー（``cc.dataset.load_candles``）として温存する（compute_controller と同規律）。
from adapter.compute import dataset  # noqa: F401
from adapter.compute import forming_bar as forming_bar_mod
from usecase.serve_candles import (
    CandlesRequest,
    CandlesResult,
    FormingBarRequest,
    FormingBarResult,
    serve_candles,
    serve_forming_bar,
)


def _error(error_type: str, message: str) -> "tuple[int, dict]":
    # エラーボディ整形は nested_error（api_shared・単一定義）へ委譲し、正典形との暗黙同期を
    #   解消する（ISSUE-104 🟡-2）。従来は violations 欠落＋series:[] 追加で正典と乖離していた。
    #   candles 固有の series:[]（系列消費側の非破壊フォールバック）は基底へ合成して温存する。
    from api_shared.http_contract import nested_error

    status, body = nested_error(error_type, message)
    body["series"] = []
    return status, body


def _present_candles(result: CandlesResult) -> "tuple[int, dict]":
    """Output Model（CandlesResult）を (HTTPステータス, ボディ) へ翻訳する（Presenter）。"""
    if not result.ok:
        return _error(result.error_type, result.error_message)
    return 200, {"ok": True, "candles": result.candles}


def _present_forming_bar(result: FormingBarResult) -> "tuple[int, dict]":
    """Output Model（FormingBarResult）を (HTTPステータス, ボディ) へ翻訳する（Presenter）。"""
    if not result.ok:
        return _error(result.error_type, result.error_message)
    return 200, {"ok": True, "bar": result.bar}


def handle_candles(ref: Any, timeframe: Any, limit_raw: Any) -> "tuple[int, dict]":
    """ローソク配信（§6.3）: datasetRef/timeframe を whitelist 検証し candles を返す。"""
    limit = int(limit_raw) if (limit_raw and str(limit_raw).isdigit()) else None
    return _present_candles(
        serve_candles(CandlesRequest(dataset_ref=ref, timeframe=timeframe, limit=limit))
    )


def handle_forming_bar(ref: Any, timeframe: Any, now_raw: Any, buffer: Any = None) -> "tuple[int, dict]":
    """形成中バー（ライブ足内更新）: ロールアップ優先 → parquet → buffer の 3 段フォールバック。

    ``{ok: True, bar: {...} | null}``。対象外 ref/tf・ティック無しは bar=null（更新なしの正常応答）。
    """
    now_override = int(now_raw) if (now_raw and str(now_raw).lstrip("-").isdigit()) else None
    return _present_forming_bar(
        serve_forming_bar(
            FormingBarRequest(
                dataset_ref=ref,
                timeframe=timeframe,
                now_override=now_override,
                buffer=buffer,
            ),
            forming_bar=forming_bar_mod,
        )
    )
