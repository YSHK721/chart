"""SOLID 是正 OCP-1: time 必須判定の宣言化（call_binding._TABLE が唯一の真実源）。

adapter（indicator_compute_adapter）は集合ハードコード ``{"tgp_btlm", "profit_band"}`` を
持たず ``requires_time(compute_id)`` を呼ぶだけ。宣言集合が従来ハードコードと完全一致する
ことを固定し、KeyError→missing_time / missing_column 翻訳の振る舞い不変を担保する回帰壁。
"""
from adapter.compute.call_binding import _TABLE, requires_time

# 従来 adapter がハードコードしていた time 必須 compute_id 集合（振る舞いの基準＝不変契約）。
_LEGACY_TIME_REQUIRED = {"tgp_btlm", "profit_band"}

# ハードコード時点より後に追加された time 必須指標。回帰壁（legacy 集合）は据え置き、
#   新規追加分はここへ明示的に列挙する（無言で壁を緩めない）。
#   cvfe: バー境界 bar_edges を実時刻から構成するため時刻軸が必須（CVFE 仕様 §3.1・§4.7-1）。
_ADDED_TIME_REQUIRED = {"cvfe"}

_TIME_REQUIRED = _LEGACY_TIME_REQUIRED | _ADDED_TIME_REQUIRED


def test_time_required_declarations_match_legacy_hardcode():
    # _TABLE の time_required 宣言集合が「従来ハードコード + 明示追加分」と完全一致する。
    #   宣言の増減はこのテストの更新を強制する（暗黙の拡大を禁じる）。
    declared = {cid for (cid, _v), spec in _TABLE.items()
                if spec.get("time_required") is True}
    assert declared == _TIME_REQUIRED
    # 従来分は 1 件も外れていないこと（振る舞い不変の回帰壁は維持する）。
    assert _LEGACY_TIME_REQUIRED <= declared


def test_requires_time_true_for_legacy_time_indicators():
    for cid in _TIME_REQUIRED:
        assert requires_time(cid) is True, cid


def test_requires_time_false_for_all_other_indicators():
    others = {cid for (cid, _v) in _TABLE if cid not in _TIME_REQUIRED}
    assert others, "テーブルに time 非必須指標が存在する前提"
    for cid in others:
        assert requires_time(cid) is False, cid


def test_requires_time_unknown_and_missing_ids_fall_back_to_false():
    assert requires_time("no_such_indicator") is False
    assert requires_time(None) is False
    assert requires_time("") is False
