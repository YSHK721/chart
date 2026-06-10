"""ComputeController.handle_compute（adapter/controller/compute_controller.py）の検証。

B方式（ライブ計算）の中核A。HTTP の殻（BaseHTTPRequestHandler）に依存しない純関数
``handle_compute(body: dict) -> tuple[int, dict]`` を検証する。datasetRef ホワイトリスト
解決 → 既存 IndicatorComputeAdapter 呼出 → (HTTPステータス, ボディ) 翻訳。

エラー型→HTTPステータス対応は内部設計書 §6.3.4 / §7.4 に準拠:
    validation/missing_column/missing_time → 400、empty_series → 422、
    backend_unavailable → 500。不正 body・未登録 indicatorId/variant・datasetRef 不正
    → 400（validation）。

import 規約: conftest.py が api/ を sys.path へ追加済み。既存 IndicatorComputeAdapter /
call_binding / 指標 src は read-only（改変しない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapter.controller.compute_controller import handle_compute  # noqa: E402


def _patch_tgp_unavailable(monkeypatch):
    """tgp バックエンド不在を環境非依存に再現する（_fitter_factory("tgp") を ImportError 化）。"""
    from adapter.compute import call_binding

    class _UnavailableTgpFitter:
        def fit_predict(self, *args, **kwargs):
            raise ImportError("rpy2 未導入（テストで tgp 不在を再現）")

    original = call_binding._fitter_factory

    def fake(name):
        return _UnavailableTgpFitter() if name == "tgp" else original(name)

    monkeypatch.setattr(call_binding, "_fitter_factory", fake)


# --------------------------------------------------------------------------- #
# 正常系（datasetRef="sample" → サンプル CSV を解決して既存アダプタ呼出）
# --------------------------------------------------------------------------- #
def test_handle_compute_tgp_btlm_ols_returns_200_with_three_line_series_unix_time():
    # Arrange
    body = {
        "indicatorId": "tgp_btlm", "variant": "default",
        "params": {"fitter": "ols", "maxbars": 40, "q_low": 0.05, "q_high": 0.95},
        "datasetRef": "sample",
    }
    # Act
    status, resp = handle_compute(body)
    # Assert
    assert status == 200
    assert resp["ok"] is True
    names = [s["name"] for s in resp["series"]]
    assert names == ["btlm_mean", "btlm_q5", "btlm_q95"]
    assert all(s["kind"] == "line" for s in resp["series"])
    first_point = resp["series"][0]["data"][0]
    assert isinstance(first_point["time"], int)  # UNIX 秒（解像度非依存）


def test_handle_compute_profit_band_global_uses_series_name():
    # Arrange
    body = {
        "indicatorId": "profit_band", "variant": "global",
        "params": {"probabilities": [0.99], "buckets": ["pOL"]},
        "datasetRef": "sample",
    }
    # Act
    status, resp = handle_compute(body)
    # Assert
    assert status == 200
    names = [s["name"] for s in resp["series"]]
    assert "pOL 99%" in names  # series_name（source_column "pOL_99" ではない）
    assert "pOL_99" not in names


def test_handle_compute_price_range_power_returns_horizontal_line():
    # Arrange
    body = {
        "indicatorId": "price_range_power", "variant": "default",
        "params": {"interval": 1.0, "top_n": 3},
        "datasetRef": "sample",
    }
    # Act
    status, resp = handle_compute(body)
    # Assert
    assert status == 200
    assert all(s["kind"] == "horizontal_line" for s in resp["series"])
    assert all(s["axis_label_visible"] is False for s in resp["series"])


# --------------------------------------------------------------------------- #
# ComputeError → HTTP ステータス対応（§6.3.4 / §7.4）
# --------------------------------------------------------------------------- #
def test_handle_compute_correlation_violation_returns_400_validation():
    # Arrange: q_low > q_high（相関制約違反）
    body = {
        "indicatorId": "tgp_btlm", "variant": "default",
        "params": {"fitter": "ols", "maxbars": 40, "q_low": 0.96, "q_high": 0.95},
        "datasetRef": "sample",
    }
    # Act
    status, resp = handle_compute(body)
    # Assert
    assert status == 400
    assert resp["ok"] is False
    assert resp["error"]["type"] == "validation"


def test_handle_compute_maps_empty_series_to_422():
    # Arrange: ComputeError("empty_series") を投げる差し替え adapter を注入し、
    # controller の error_type→HTTPステータス対応（empty_series→422・§6.3.4）を検証する。
    # （実 src で empty_series を起こすデータ依存を排し、controller の翻訳分岐のみを純粋に検証）
    from adapter.compute import ComputeError

    class _EmptyAdapter:
        def compute(self, compute_id, variant, df, params):
            raise ComputeError("empty_series", "必須バケットが空です。")

    body = {
        "indicatorId": "profit_band", "variant": "global",
        "params": {"probabilities": [0.99]},
        "datasetRef": "sample",
    }
    # Act
    status, resp = handle_compute(body, adapter=_EmptyAdapter())
    # Assert
    assert status == 422
    assert resp["error"]["type"] == "empty_series"


def test_handle_compute_tgp_backend_unavailable_returns_500(monkeypatch):
    # Arrange: tgp バックエンド不在を再現 → backend_unavailable（500）
    _patch_tgp_unavailable(monkeypatch)
    body = {
        "indicatorId": "tgp_btlm", "variant": "default",
        "params": {"fitter": "tgp", "maxbars": 40},
        "datasetRef": "sample",
    }
    # Act
    status, resp = handle_compute(body)
    # Assert
    assert status == 500
    assert resp["error"]["type"] == "backend_unavailable"


# --------------------------------------------------------------------------- #
# datasetRef ホワイトリスト（§7.3 パストラバーサル対策）
# --------------------------------------------------------------------------- #
def test_handle_compute_unknown_dataset_ref_returns_400_validation():
    # Arrange: 未知の datasetRef キー
    body = {
        "indicatorId": "tgp_btlm", "variant": "default",
        "params": {"fitter": "ols", "maxbars": 40},
        "datasetRef": "unknown_dataset",
    }
    # Act
    status, resp = handle_compute(body)
    # Assert
    assert status == 400
    assert resp["error"]["type"] == "validation"


def test_handle_compute_path_traversal_dataset_ref_returns_400_validation():
    # Arrange: パストラバーサル文字列（生パス直送）を datasetRef に渡す
    body = {
        "indicatorId": "tgp_btlm", "variant": "default",
        "params": {"fitter": "ols", "maxbars": 40},
        "datasetRef": "../../../etc/passwd",
    }
    # Act
    status, resp = handle_compute(body)
    # Assert: サーバ側パスを外から組み立てない（400 で拒否）
    assert status == 400
    assert resp["error"]["type"] == "validation"


# --------------------------------------------------------------------------- #
# 不正 body / 未登録 indicatorId・variant
# --------------------------------------------------------------------------- #
def test_handle_compute_missing_indicator_id_returns_400():
    # Arrange: 必須 indicatorId 欠落
    body = {"variant": "default", "params": {}, "datasetRef": "sample"}
    # Act
    status, resp = handle_compute(body)
    # Assert
    assert status == 400
    assert resp["error"]["type"] == "validation"


def test_handle_compute_unknown_indicator_id_returns_400():
    # Arrange: 未登録 indicatorId
    body = {
        "indicatorId": "does_not_exist", "variant": "default",
        "params": {}, "datasetRef": "sample",
    }
    # Act
    status, resp = handle_compute(body)
    # Assert
    assert status == 400
    assert resp["error"]["type"] == "validation"


def test_handle_compute_unknown_variant_returns_400():
    # Arrange: 登録 indicatorId だが未登録 variant（CallBinding.resolve が raw KeyError）
    body = {
        "indicatorId": "tgp_btlm", "variant": "nope",
        "params": {"fitter": "ols", "maxbars": 40}, "datasetRef": "sample",
    }
    # Act
    status, resp = handle_compute(body)
    # Assert
    assert status == 400
    assert resp["error"]["type"] == "validation"


def test_handle_compute_accepts_compute_id_alias_for_indicator_id():
    # Arrange: indicatorId の別名 compute_id を受理する（設計の入力柔軟性）
    body = {
        "compute_id": "tgp_btlm", "variant": "default",
        "params": {"fitter": "ols", "maxbars": 40, "q_low": 0.05, "q_high": 0.95},
        "datasetRef": "sample",
    }
    # Act
    status, resp = handle_compute(body)
    # Assert
    assert status == 200
    assert [s["name"] for s in resp["series"]] == ["btlm_mean", "btlm_q5", "btlm_q95"]


# --------------------------------------------------------------------------- #
# 時間足（timeframe）・直近 N 本（limit）— §チャート表示時間選択 / 配信設計
# --------------------------------------------------------------------------- #
def test_handle_compute_unknown_timeframe_returns_400():
    # 未知 timeframe コードは datasetRef 同様に拒否（§7.3 ホワイトリスト方針）。
    body = {
        "indicatorId": "tgp_btlm", "variant": "default",
        "params": {"fitter": "ols", "maxbars": 40, "q_low": 0.05, "q_high": 0.95},
        "datasetRef": "sample", "timeframe": "9z",
    }
    status, resp = handle_compute(body)
    assert status == 400
    assert resp["error"]["type"] == "validation"


def test_handle_compute_weekly_timeframe_returns_200():
    # 既知 timeframe（1W）は受理し、resample 済み DataFrame で計算できる。
    body = {
        "indicatorId": "tgp_btlm", "variant": "default",
        "params": {"fitter": "ols", "maxbars": 40, "q_low": 0.05, "q_high": 0.95},
        "datasetRef": "sample", "timeframe": "1W",
    }
    status, resp = handle_compute(body)
    assert status == 200
    assert [s["name"] for s in resp["series"]] == ["btlm_mean", "btlm_q5", "btlm_q95"]


def test_handle_compute_limit_restricts_computation_window():
    # limit=N は直近 N 本へ制限する（candles と同一窓で計算・§配信設計）。系列点数が limit 以下。
    body = {
        "indicatorId": "tgp_btlm", "variant": "default",
        "params": {"fitter": "ols", "maxbars": 200, "q_low": 0.05, "q_high": 0.95},
        "datasetRef": "sample", "timeframe": "1D", "limit": 30,
    }
    status, resp = handle_compute(body)
    assert status == 200
    # 計算窓が直近 30 本に制限されるため、各系列の点数は 30 以下。
    assert all(len(s["data"]) <= 30 for s in resp["series"])


# numpy は ramp フィクスチャ生成の将来拡張用 import（現テストでは controller 内で生成）。
_ = (np, pd)
