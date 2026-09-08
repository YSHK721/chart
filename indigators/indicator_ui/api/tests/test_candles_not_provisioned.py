"""/candles の素材未配備の分類（ISSUE-474）。

登録済み ref の M1/ロールアップ CSV が未配備のとき、``FileNotFoundError`` が
serve_candles の包括 except で ``internal``（500）に潰れ、構成状態（未配備）と障害
（バグ・破損）を区別できなかった。さらにメッセージが例外の str 表現任せで、
「実際に開こうとした経路」が構造化されずに埋まっていた（診断性の罠）。

固定する不変条件:
  R1: FileNotFoundError は「未配備（not_provisioned）」へ分類され、4xx（404）で返る。
  R2: メッセージに datasetRef / timeframe と**実際に開こうとした経路**（exc.filename）が載る。
  R3: それ以外の例外は従来どおり internal（500）のまま（分類の取り違えをしない）。
"""
from __future__ import annotations

import pytest

from adapter.controller import candles_controller as cc
from usecase.serve_candles import CandlesRequest, serve_candles

_MISSING = "/workspaces/app/data/marketdata/rollups/jp225_mt5_5m.csv"


class _RaisingPort:
    """検証は通し、load_candles だけ指定例外を投げるフェイク（未配備 ref の実型）。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def is_known(self, ref):
        return True

    def is_known_timeframe(self, timeframe):
        return True

    def load_candles(self, ref, timeframe, limit):
        raise self._exc


def test_file_not_found_is_classified_as_not_provisioned():
    """R1/R2（usecase）: FileNotFoundError → not_provisioned・経路入りメッセージ。"""
    exc = FileNotFoundError(2, "No such file or directory", _MISSING)
    result = serve_candles(
        CandlesRequest(dataset_ref="jp225_mt5", timeframe="5m", limit=100),
        dataset_port=_RaisingPort(exc),
    )
    assert result.error_type == "not_provisioned", (
        f"未配備は internal と区別する（実際: {result.error_type!r}）"
    )
    assert _MISSING in (result.error_message or ""), (
        f"実際に開こうとした経路をメッセージへ載せる（実際: {result.error_message!r}）"
    )
    assert "jp225_mt5" in result.error_message and "5m" in result.error_message


def test_file_not_found_without_filename_still_carries_the_str():
    """R2 縮退: filename 属性の無い FileNotFoundError でも str(exc) で経路情報を残す。"""
    result = serve_candles(
        CandlesRequest(dataset_ref="jp225_mt5", timeframe="5m"),
        dataset_port=_RaisingPort(FileNotFoundError(f"missing: {_MISSING}")),
    )
    assert result.error_type == "not_provisioned"
    assert _MISSING in (result.error_message or "")


def test_not_provisioned_maps_to_404_not_500(monkeypatch):
    """R1（presenter まで通し）: 未配備は 404・nested 形・type=not_provisioned で返る。"""
    def _raise(ref, tf, limit):
        raise FileNotFoundError(2, "No such file or directory", _MISSING)

    monkeypatch.setattr(cc.dataset, "load_candles", _raise)
    st, body = cc.handle_candles("jp225_mt5", "5m", "100")
    assert st == 404, f"未配備は 4xx（実測前は internal 500 だった・実際: {st}）"
    assert body["ok"] is False
    assert body["error"]["type"] == "not_provisioned"
    assert _MISSING in body["error"]["message"]


@pytest.mark.parametrize("exc", [ValueError("broken csv"), RuntimeError("boom")])
def test_other_failures_remain_internal_500(monkeypatch, exc):
    """R3: 未配備以外の失敗は従来どおり internal（500）のまま。"""
    monkeypatch.setattr(cc.dataset, "load_candles", lambda *a: (_ for _ in ()).throw(exc))
    st, body = cc.handle_candles("jp225_mt5", "5m", "100")
    assert st == 500
    assert body["error"]["type"] == "internal"
