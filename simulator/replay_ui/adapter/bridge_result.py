"""indicator_ui bridge の ``(status, body)`` を :class:`PortResult` へ翻訳する（adapter 層）。

## 立ち位置

4 つの gateway（catalog / market_profile / market_profile_forming / tickvol_profile）は
いずれも indicator_ui の純ロジック controller を read-only 再利用する。その controller は
**HTTP 殻のために** ``(status, body)`` を返す（catalog / market_profile ほかの handler）。それは
replay_ui から見れば「外部システムの表現」であり、その表現を内側の語彙へ直すのが
adapter の仕事である（内側＝usecase は :class:`PortResult` しか知らない・DIP）。

翻訳は 4 gateway で同一なので、ここ 1 箇所にだけ書く（手書き複製をしない）。

## 分類は**ボディから**採る（ステータス番号から逆算しない）

``ERROR_STATUS`` は分類 → 番号の写像で、**単射ではない**（validation /
missing_column / missing_time はいずれも 400）。番号から分類を逆算すると、
どれか 1 つへ丸めることになり、front が読む ``error.type`` が書き換わる。
分類は正典 nested error ボディ（``{"ok": false, "error": {"type": ...}}``）が
既に持っているので、そこから採る。

## 食い違いは通さない（fail-closed）

bridge が返した番号と、採った分類から正典表が定める番号が食い違ったら、それは
「外部の表現が想定と違う」ことであり、黙って通すと応答 byte が静かに変わる。
その場で :class:`BridgeContractError` を送出して止める（framework の中央翻訳器が
``internal`` として扱う）。

実測（2026-09-07・是正時に全 return 箇所を確認）: 4 controller が返す組は
「200 と ok=True のボディ」か「正典 nested error」のみである
（``catalog_controller.py:26,32`` / ``market_profile_controller.py:188,379,432,484`` /
``market_profile_forming_controller.py:200,205,271`` /
``tickvol_profile_controller.py:34-44``）。したがって本検査は本番で発火しない——
発火するとしたら、それは外部の表現が変わったときである。
"""
from __future__ import annotations

from typing import Any

from api_shared.http_contract import ERROR_STATUS

from simulator.replay_ui.usecase.port_result import PortResult

#: 成功の HTTP ステータス（bridge 側の ``return 200, body`` に対応する唯一の値）。
_OK_STATUS = 200


class BridgeContractError(RuntimeError):
    """bridge の ``(status, body)`` が正典契約と食い違ったときに送出する（fail-closed）。"""


def _classification_of(payload: Any) -> "str | None":
    """ボディから失敗の分類を採る（成功なら None）。

    正典 nested error は ``{"ok": False, "generation": .., "error": {"type": ..}}``。
    ボディの成否キーが False なのに分類が読めないときは、分類の欠落として internal に落とす
    （成功として通さない）。
    """
    if not isinstance(payload, dict) or payload.get("ok") is not False:
        return None
    error = payload.get("error")
    error_type = error.get("type") if isinstance(error, dict) else None
    return error_type if isinstance(error_type, str) and error_type else "internal"


def port_result_from_bridge(response: Any, *, source: str) -> PortResult:
    """bridge の ``(status, body)`` を :class:`PortResult` へ翻訳する。

    Args:
        response: bridge handler の戻り値（``(status, body)``）。
        source: 食い違いを報告するときに出す呼び出し元の名前（例 ``"handle_catalog"``）。

    Raises:
        BridgeContractError: 組の形が違う／番号と分類が食い違う。
    """
    if not (isinstance(response, tuple) and len(response) == 2):
        raise BridgeContractError(
            f"{source} が (status, body) の組を返していません: {type(response)!r}"
        )
    status, payload = response
    error_type = _classification_of(payload)
    expected = _OK_STATUS if error_type is None else ERROR_STATUS.get(error_type, 500)
    if status != expected:
        raise BridgeContractError(
            f"{source} のステータスが正典契約と食い違います: "
            f"status={status!r} / 分類={error_type!r} / 正典={expected!r}"
        )
    if error_type is None:
        return PortResult.success(payload)
    return PortResult.failure(error_type, payload)
