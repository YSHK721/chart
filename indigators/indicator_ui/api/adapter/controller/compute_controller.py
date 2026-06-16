"""ComputeController（内部設計書 §3.3.5）— POST /compute の純ロジック。

``handle_compute(body) -> (HTTPステータス, ボディ)`` は HTTP の殻
（BaseHTTPRequestHandler・ソケット）に依存しない純関数である。HTTP サーバ本体は
``api/framework/server.py`` が本関数を呼ぶ薄い殻として実装する。

処理（§4.5 / §6.3 / §7.3 / §7.4）:
  1. body から indicatorId（別名 compute_id 許容）/ variant / params / datasetRef を取り出す。
  2. datasetRef をホワイトリスト解決する（未知キー・パス文字列は 400 で拒否＝§7.3
     パストラバーサル対策。サーバ側パスを外から組み立てない）。
  3. 解決したパスを既存 loader で DataFrame 化（adapter.compute.dataset へ集約）。
  4. 既存 ``IndicatorComputeAdapter.compute`` を呼び、収集系列を得る。
  5. 成功は (200, {ok, generation, series})。ComputeError は §6.3.4 の error.type →
     HTTPステータス対応（adapter.compute.ERROR_STATUS・単一定義）で翻訳する。

stdlib のみと既存 adapter/loader を用いる。Flask 等は導入しない。
既存 IndicatorComputeAdapter / call_binding / 指標 src は read-only（改変しない）。
"""

from __future__ import annotations

from typing import Any

from adapter.compute import ERROR_STATUS, ComputeError, IndicatorComputeAdapter
from adapter.compute import dataset
from adapter.compute.latest_dispatch import full_compute, latest_compute


def _error_body(generation: int, error_type: str, message: str) -> tuple[int, dict[str, Any]]:
    """§6.3.4 エラーボディ（{ok:false, generation, error:{type, message, violations}}）。

    error_type→HTTPステータスは adapter.compute.ERROR_STATUS（単一定義）を参照する。
    """
    status = ERROR_STATUS.get(error_type, 500)
    return status, {
        "ok": False,
        "generation": generation,
        "error": {"type": error_type, "message": message, "violations": []},
    }


def handle_compute(
    body: dict[str, Any], *, adapter: Any | None = None
) -> tuple[int, dict[str, Any]]:
    """POST /compute の純ロジック（§4.5 / §6.3 / §7.3 / §7.4）。

    Args:
        body: リクエストボディ（indicatorId|compute_id / variant / params / datasetRef）。
        adapter: IndicatorComputeAdapter 互換オブジェクト（テスト注入用。既定は実 adapter）。

    Returns:
        (HTTPステータス, レスポンスボディ)。成功は (200, {ok, generation, series})、
        失敗は (4xx/5xx, {ok:false, generation, error})。
    """
    generation = body.get("generation", 0)

    # indicatorId（別名 compute_id 許容）と variant の取り出し・入口検証。
    indicator_id = body.get("indicatorId") or body.get("compute_id")
    variant = body.get("variant")
    if not indicator_id or not variant:
        return _error_body(generation, "validation", "indicatorId と variant は必須です。")

    params = body.get("params") or {}
    dataset_ref = body.get("datasetRef")

    # datasetRef ホワイトリスト解決（§7.3）。未知キー・パス文字列は拒否。
    if not dataset.is_known(dataset_ref):
        return _error_body(
            generation, "validation", f"未知の datasetRef です: {dataset_ref!r}"
        )

    # timeframe（時間足）— None は原子（再集計なし・後方互換）。未知コードは拒否（§7.3 同様）。
    timeframe = body.get("timeframe")
    if timeframe is not None and not dataset.is_known_timeframe(timeframe):
        return _error_body(
            generation, "validation", f"未知の timeframe です: {timeframe!r}"
        )

    df = dataset.load_dataframe(dataset_ref, timeframe)

    # 表示範囲制限（直近 N 本）。1 分足原子の全期間で指標計算しないための制限
    # （§配信設計: リサンプル＋直近 N 本）。candles と同一窓で計算し時間軸を揃える。
    limit = body.get("limit")
    if isinstance(limit, int) and limit > 0:
        df = df.tail(limit)

    # mode（計算モード）: 省略=full＝既存挙動（既存テスト無変更で緑）。"latest" は
    #   Latest 増分計算（archetype ごとに tail＋末尾K切り・latest_dispatch に集約）。
    #   limit の tail は不変（min_window <= limit 前提）。
    mode = body.get("mode", "full")
    compute_adapter = adapter or IndicatorComputeAdapter()
    try:
        series = (
            latest_compute(compute_adapter, indicator_id, variant, df, dict(params))
            if mode == "latest"
            else full_compute(compute_adapter, indicator_id, variant, df, dict(params))
        )
    except ComputeError as exc:
        return _error_body(generation, exc.error_type, exc.message)
    except KeyError as exc:
        # 未登録 indicatorId/variant は CallBinding.resolve が raw KeyError を投げる（§3.3.3）。
        return _error_body(
            generation, "validation", f"未登録の指標または variant です: {exc}"
        )

    return 200, {"ok": True, "generation": generation, "series": series}
