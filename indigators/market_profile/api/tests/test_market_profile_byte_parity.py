"""MP backend 移設の byte 一致回帰ゲート（Phase 1）。

MP 計算＋HTTP ハンドラを ``indigators/indicator_ui/api/adapter`` から新モジュール
``indigators/market_profile/api``（トップパッケージ ``market_profile_api``）へ切り出した際、
``handle_market_profile`` / ``handle_market_profile_forming`` の応答が **移設前と byte 一致** する
ことを固定 golden で保証する。

golden（``fixtures/mp_byte_parity_golden.jsonl``・1 行 1 ケース）は移設前の OLD ハンドラ
（``adapter.controller.market_profile_*``）で同一クエリ集合を走らせ
``(status, json.dumps(body, sort_keys=True))`` を記録したもの。本テストは新パッケージの
ハンドラに **同一クエリ** を通し、status と正規化 JSON body の完全一致を assert する。
（フォーマットは repo 規約に合わせ非 JSON 拡張＝JSONL を用いる。``*.json`` は .gitignore で
除外されるため。）

クエリ集合（golden 内 ``q`` に保存・順序固定）:
  - candle: ref=sample × {default/bins/limit/va/barw/to/from/sessions/today/want_fine}
  - dwell(src=dwell) / m1(src=m1): ref=jp225_tick × {to/from/barw/bins}
  - forming: ref=jp225_tick × {base/now/frm/tf=15m/since}
  - 異常系(400): 未知 ref / 未知 tf / 非 tick ref での dwell・forming
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# 新パッケージ（market_profile_api）— conftest が sys.path を結線する。
from market_profile_api.controller.market_profile_controller import handle_market_profile
from market_profile_api.controller.market_profile_forming_controller import (
    handle_market_profile_forming,
)

_GOLDEN_PATH = Path(__file__).resolve().parent / "fixtures" / "mp_byte_parity_golden.jsonl"


def _load_golden() -> list[dict]:
    return [json.loads(line) for line in _GOLDEN_PATH.read_text().splitlines() if line.strip()]


def _run_case(entry: dict) -> tuple[int, str]:
    """golden の 1 ケースを新ハンドラで再実行し (status, 正規化 body) を返す。"""
    q = dict(entry["q"])
    ref = q.pop("ref")
    if entry["h"] == "mp":
        status, body = handle_market_profile(ref, **q)
    elif entry["h"] == "forming":
        timeframe = q.pop("timeframe")
        status, body = handle_market_profile_forming(ref, timeframe, **q)
    else:  # pragma: no cover - golden 破損時のみ
        raise AssertionError(f"unknown handler kind: {entry['h']!r}")
    return status, json.dumps(body, sort_keys=True, default=str)


def test_golden_fixture_is_present_and_covers_all_paths():
    """回帰ゲートの前提: golden が存在し candle/dwell/m1/forming・正常/異常を網羅する。"""
    golden = _load_golden()
    assert len(golden) >= 20  # 主要パスを覆う最低ケース数。
    kinds = {e["h"] for e in golden}
    assert kinds == {"mp", "forming"}
    statuses = {e["status"] for e in golden}
    assert 200 in statuses and 400 in statuses  # 正常系・異常系の双方を含む。


@pytest.mark.parametrize("entry", _load_golden(), ids=lambda e: f"{e['h']}:{e['q']}")
def test_handler_response_is_byte_identical_to_pre_migration_golden(entry):
    """移設後の新ハンドラ応答が移設前 golden と status＋正規化 JSON body で byte 一致する。"""
    status, body_json = _run_case(entry)
    assert status == entry["status"]
    assert body_json == entry["body"]
