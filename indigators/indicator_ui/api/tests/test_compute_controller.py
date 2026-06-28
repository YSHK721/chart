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

    def fake(name, samples="standard"):
        return _UnavailableTgpFitter() if name == "tgp" else original(name, samples)

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


# --------------------------------------------------------------------------- #
# mode 分岐（full / latest）— Latest 増分計算フレームワーク（Stage A 基盤）
# --------------------------------------------------------------------------- #
def test_handle_compute_mode_omitted_is_full_backward_compatible():
    # mode 省略は full（既存挙動）。moving_averages の MA 系列が全件分の点数を持つ。
    body = {
        "indicatorId": "moving_averages", "variant": "default",
        "params": {"ma_type": "sma", "length": 9, "source": "close",
                   "smoothing_type": "none", "wait_for_close": False},
        "datasetRef": "sample", "timeframe": "1D", "limit": 60,
    }
    status, resp = handle_compute(body)
    assert status == 200
    ma = next(s for s in resp["series"] if s["name"] == "MA")
    assert len(ma["data"]) > 1  # full は複数点


def test_handle_compute_mode_full_explicit_matches_omitted():
    # mode="full" は mode 省略と同一 series（後方互換）。
    base = {
        "indicatorId": "moving_averages", "variant": "default",
        "params": {"ma_type": "sma", "length": 9, "source": "close",
                   "smoothing_type": "none", "wait_for_close": False},
        "datasetRef": "sample", "timeframe": "1D", "limit": 60,
    }
    _, resp_omitted = handle_compute(dict(base))
    _, resp_full = handle_compute({**base, "mode": "full"})
    assert resp_full["series"] == resp_omitted["series"]


def test_handle_compute_mode_latest_trims_line_to_trailing_k():
    # mode="latest" は line 系列を末尾 K 点（moving_averages は K=1）へ切る。
    body = {
        "indicatorId": "moving_averages", "variant": "default",
        "params": {"ma_type": "sma", "length": 9, "source": "close",
                   "smoothing_type": "none", "wait_for_close": False},
        "datasetRef": "sample", "timeframe": "1D", "limit": 60, "mode": "latest",
    }
    status, resp = handle_compute(body)
    assert status == 200
    ma = next(s for s in resp["series"] if s["name"] == "MA")
    assert len(ma["data"]) == 1  # 末尾 K=1


def test_handle_compute_mode_latest_line_tail_equals_full_tail():
    # mode="latest" の末尾点が mode="full" の末尾点と一致（不変条件の controller 経路）。
    base = {
        "indicatorId": "moving_averages", "variant": "default",
        "params": {"ma_type": "sma", "length": 9, "source": "close",
                   "smoothing_type": "none", "wait_for_close": False},
        "datasetRef": "sample", "timeframe": "1D", "limit": 60,
    }
    _, resp_full = handle_compute({**base, "mode": "full"})
    _, resp_latest = handle_compute({**base, "mode": "latest"})
    ma_full = next(s for s in resp_full["series"] if s["name"] == "MA")
    ma_latest = next(s for s in resp_latest["series"] if s["name"] == "MA")
    assert ma_latest["data"] == ma_full["data"][-1:]


def test_handle_compute_mode_latest_price_range_power_keeps_horizontal_line():
    # axis_distribution は latest でも horizontal_line を全件返す（末尾K切りしない）。
    body = {
        "indicatorId": "price_range_power", "variant": "default",
        "params": {"interval": 1.0, "top_n": 3},
        "datasetRef": "sample", "timeframe": "1D", "limit": 120, "mode": "latest",
    }
    status, resp = handle_compute(body)
    assert status == 200
    assert all(s["kind"] == "horizontal_line" for s in resp["series"])


# numpy は ramp フィクスチャ生成の将来拡張用 import（現テストでは controller 内で生成）。
_ = (np, pd)


# --------------------------------------------------------------------------- #
# ライブ足内更新（指標）: mode="latest" 時の形成中バー注入（ティック由来）
# --------------------------------------------------------------------------- #
from adapter.controller import compute_controller as _cc  # noqa: E402


def _stub_df():
    idx = pd.DatetimeIndex([pd.Timestamp("2025-01-02 09:00:00")], name="date")
    return pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]}, index=idx
    )


def test_latest_applies_forming_bar_for_tick_ref_with_formingNow(monkeypatch):
    seen = {}
    monkeypatch.setattr(_cc.dataset, "load_dataframe", lambda ref, tf: _stub_df())
    monkeypatch.setattr(_cc, "latest_compute", lambda *a, **k: [])
    monkeypatch.setattr(_cc.forming_bar_mod, "apply_forming_bar",
                        lambda df, ref, tf, now: (seen.update(ref=ref, tf=tf, now=now), df)[1])
    status, body = handle_compute({
        "indicatorId": "x", "variant": "default", "datasetRef": "jp225_tick",
        "timeframe": "5m", "mode": "latest", "formingNow": 123,
    })
    assert status == 200 and body["ok"] is True
    assert seen == {"ref": "jp225_tick", "tf": "5m", "now": 123}  # formingNow を now に採用。


def test_latest_resolves_now_via_provider_when_no_formingNow(monkeypatch):
    seen = {}
    monkeypatch.setattr(_cc.dataset, "load_dataframe", lambda ref, tf: _stub_df())
    monkeypatch.setattr(_cc, "latest_compute", lambda *a, **k: [])
    # now は forming_bar.resolve_now_unix へ一元化（formingNow 無しは provider が解決）。
    monkeypatch.setattr(_cc.forming_bar_mod, "resolve_now_unix", lambda override: 999 if override is None else override)
    monkeypatch.setattr(_cc.forming_bar_mod, "apply_forming_bar",
                        lambda df, ref, tf, now: (seen.update(now=now), df)[1])
    handle_compute({"indicatorId": "x", "variant": "default", "datasetRef": "jp225_tick",
                    "timeframe": "5m", "mode": "latest"})
    assert seen["now"] == 999  # provider 解決値（formingNow 不在）。


def test_full_mode_does_not_apply_forming_bar(monkeypatch):
    seen = {"called": False}
    monkeypatch.setattr(_cc.dataset, "load_dataframe", lambda ref, tf: _stub_df())
    monkeypatch.setattr(_cc, "full_compute", lambda *a, **k: [])
    monkeypatch.setattr(_cc.forming_bar_mod, "apply_forming_bar",
                        lambda *a, **k: seen.update(called=True))
    handle_compute({"indicatorId": "x", "variant": "default", "datasetRef": "jp225_tick",
                    "timeframe": "5m"})  # mode 省略=full
    assert seen["called"] is False  # 履歴計算は形成中バーを注入しない（後方互換）。
