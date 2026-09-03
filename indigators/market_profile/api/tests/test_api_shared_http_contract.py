"""api_shared.http_contract の移設回帰（ISSUE-094 🔵-11 / ISSUE-479 F-8 段階 1）。

HTTP 契約（ERROR_STATUS / nested_error）を中立共有パッケージ api_shared へ移設し、
marketdata.api_contract を後方互換の再エクスポートへ降格した。ISSUE-479 F-8 段階 1 で旧パスの
参照はゼロになった（本テストが最後の消費者の 1 つだった）ため、旧パス経由の同一性検査は
「所有者側を 2 度参照するだけの恒真式」に退化する。そこで旧パス依存の 2 検査を、所有者
モジュールに対する実質的な検査（公開面の固定・呼出ごとの独立性）へ置き換えた。
本テストは (1) 実体が api_shared.http_contract にあること、(2) その公開面と純関数性、
(3) nested_error の (status, body) が移設前と byte 等価であることを固定する。

旧パスを import する箇所が 0 件であることは marketdata/tests/test_no_legacy_api_contract_reference.py
が保証する。marketdata/api_contract.py の削除は要承認事項（段階 2）。
"""
from __future__ import annotations

import api_shared.http_contract as http_contract


def test_error_status_table_is_the_expected_mapping() -> None:
    assert http_contract.ERROR_STATUS == {
        "validation": 400,
        "missing_column": 400,
        "missing_time": 400,
        "empty_series": 422,
        "backend_unavailable": 500,
        "internal": 500,
    }


def test_owner_module_exposes_exactly_the_contract_surface() -> None:
    """所有者モジュールの公開面は契約 2 点のみ（第 2 の入口・付随物を生やさない）。

    ISSUE-479 F-8: 旧パス経由の同一性検査（``api_contract.X is http_contract.X``）は参照ゼロ化に
    伴い恒真式へ退化するため、実質的な検査＝所有者の公開面の固定へ置き換えた。
    識別力: 契約表・整形関数の追加／改名／撤去のいずれでも Red になる。
    """
    contract = {"ERROR_STATUS", "nested_error"}
    public = {n for n in vars(http_contract) if not n.startswith("_")}

    assert contract <= public, "契約 2 点が公開面から消えています"
    assert public - contract == {"annotations", "Any"}, (
        "契約表・整形関数以外の実装物が公開面に現れています（型注釈のための import 以外は置かない）:"
        f" {sorted(public - contract - {'annotations', 'Any'})}"
    )
    assert isinstance(http_contract.ERROR_STATUS, dict)
    assert callable(http_contract.nested_error)


def test_nested_error_validation_shape_unchanged() -> None:
    status, body = http_contract.nested_error("validation", "bad input")
    assert status == 400
    assert body == {
        "ok": False,
        "generation": 0,
        "error": {"type": "validation", "message": "bad input", "violations": []},
    }


def test_nested_error_unknown_type_falls_back_to_500() -> None:
    status, body = http_contract.nested_error("nope", "x", generation=7)
    assert status == 500
    assert body["generation"] == 7
    assert body["error"]["type"] == "nope"


def test_nested_error_is_pure_and_returns_independent_bodies() -> None:
    """同じ入力に同じ出力を返し、かつ呼出ごとに独立したボディを返す（純関数・共有状態なし）。

    ISSUE-479 F-8: 旧パス経由の出力同一性検査（同じ関数を 2 回呼んで比べるだけ）は参照ゼロ化に
    伴い恒真式へ退化するため、実質的な検査＝純関数性と可変状態の非共有へ置き換えた。
    識別力: ERROR_STATUS やボディの violations 欄をモジュール定数として使い回す実装に退行すると
    （呼び出し側の変更が次の応答へ漏れるため）Red になる。
    """
    first = http_contract.nested_error("internal", "boom", generation=3)
    second = http_contract.nested_error("internal", "boom", generation=3)

    assert first == second                       # 同入力・同出力（純関数）
    assert first[1] is not second[1]             # ボディは呼出ごとに新しい
    assert first[1]["error"]["violations"] is not second[1]["error"]["violations"]

    first[1]["error"]["violations"].append("mutated by the caller")
    assert http_contract.nested_error("internal", "boom", generation=3) == second
