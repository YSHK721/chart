"""ComputeController（内部設計書 §3.3.5）— POST /compute の薄殻（Controller + Presenter）。

``handle_compute(body) -> (HTTPステータス, ボディ)`` は HTTP の殻
（BaseHTTPRequestHandler・ソケット）に依存しない純関数である。HTTP サーバ本体は
``api/framework/server.py`` が本関数を呼ぶ薄い殻として実装する。

ISSUE-092 ①: 業務手順（datasetRef ホワイトリスト検証→データロード→forming_bar→指標計算→
エラー翻訳）は usecase 純関数 :func:`usecase.compute_indicators.compute_indicators` へ移設した。
本 controller は次の 2 責務のみを担う薄殻へ縮退している:

  - Controller: リクエストボディを Input Model（ComputeRequest）へ変換して usecase を呼ぶ。
  - Presenter: usecase の Output Model（ComputeResult）を (HTTPステータス, レスポンスボディ) へ
    翻訳する（エラーボディは api_shared.http_contract.nested_error・単一定義）。

usecase へ渡す協調子（forming_bar / full_compute / latest_compute / ComputeError）は本 module の
名前解決を通す（呼出時に module グローバルを参照）。これにより既存テストの monkeypatch 経路
（``_cc.dataset.load_dataframe`` / ``_cc.latest_compute`` / ``_cc.full_compute`` /
``_cc.forming_bar_mod.*``）は不変のまま温存される。datasetRef のロードは usecase 所有の
Output Boundary（DatasetPort）＋既定 gateway（marketdata.dataset へ委譲）が担う。

stdlib のみと既存 adapter/loader を用いる。Flask 等は導入しない。
既存 IndicatorComputeAdapter / call_binding / 指標 src は read-only（改変しない）。
"""

from __future__ import annotations

from typing import Any

from adapter.compute import ComputeError, IndicatorComputeAdapter
from marketdata import dataset  # noqa: F401  # monkeypatch 対象（_cc.dataset）＋既定 gateway の委譲先。
from adapter.compute import forming_bar as forming_bar_mod
from adapter.compute.latest_dispatch import full_compute, latest_compute
from adapter.compute.mtf_causal_frames import causal_mtf_frames
from usecase.compute_indicators import ComputeRequest, ComputeResult, compute_indicators


def _present(result: ComputeResult) -> tuple[int, dict[str, Any]]:
    """Output Model（ComputeResult）を (HTTPステータス, ボディ) へ翻訳する（Presenter）。

    成功は (200, {ok, generation, series})。失敗は nested_error（api_shared.http_contract・
    単一定義）で §6.3.4 のエラーボディへ翻訳する（ISSUE-104 🟡-2: ボディ整形の暗黙同期を解消）。
    """
    if result.ok:
        return 200, {
            "ok": True,
            "generation": result.generation,
            "series": result.series,
        }
    from api_shared.http_contract import nested_error

    return nested_error(result.error_type, result.error_message, generation=result.generation,
                        violations=getattr(result, "error_violations", None))


def _causal_mtf(adapter: Any, body: dict[str, Any]):
    """usecase へ渡す上位足協調子（DataFrame 境界＋各バーの latest 計算）を作る。

    指標 id / variant / params は要求から決まるため、ここで束ねてから渡す（usecase は
    「C と H を渡せば因果系列が返る」ことだけを知る＝pandas も指標も知らない）。
    """
    from marketdata.tf_meta import bar_time_unix

    indicator_id = body.get("indicatorId") or body.get("compute_id")
    variant = body.get("variant")
    params = dict(body.get("params") or {})

    def _run(*, df_chart, df_source, compute_tf, fold_from=None):
        return causal_mtf_frames(
            df_chart=df_chart,
            df_source=df_source,
            compute_tf=compute_tf,
            bar_time_unix=bar_time_unix,
            compute_latest=lambda df: latest_compute(
                adapter, indicator_id, variant, df, dict(params)),
            fold_from=fold_from,
        )

    return _run


def handle_compute(
    body: dict[str, Any], *, adapter: Any | None = None
) -> tuple[int, dict[str, Any]]:
    """POST /compute の薄殻（§4.5 / §6.3 / §7.3 / §7.4）。

    Args:
        body: リクエストボディ（indicatorId|compute_id / variant / params / datasetRef）。
        adapter: IndicatorComputeAdapter 互換オブジェクト（テスト注入用。既定は実 adapter）。

    Returns:
        (HTTPステータス, レスポンスボディ)。成功は (200, {ok, generation, series})、
        失敗は (4xx/5xx, {ok:false, generation, error})。
    """
    request = ComputeRequest.from_body(body)
    compute_adapter = adapter or IndicatorComputeAdapter()
    # 協調子は本 module の名前解決を通す（monkeypatch 経路の温存）。dataset のロードは
    #   usecase 既定（DatasetPort 遅延既定 gateway＝marketdata.dataset 委譲）に委ねる。
    result = compute_indicators(
        request,
        compute_adapter=compute_adapter,
        forming_bar=forming_bar_mod,
        full_compute=full_compute,
        latest_compute=latest_compute,
        compute_error=ComputeError,
        # ISSUE-274 → 295: 上位足は「各バー τ の時点で計算できた値」を返す因果系列にする
        #   （ライブ・リプレイ共通の唯一源 adapter.compute.mtf_causal）。期間ラベルの唯一源は
        #   marketdata.tf_meta.bar_time_unix（ロールアップ／形成中バーと同じ参照経路）。
        project_mtf=_causal_mtf(compute_adapter, body),
        period_boundary=forming_bar_mod,
    )
    return _present(result)
