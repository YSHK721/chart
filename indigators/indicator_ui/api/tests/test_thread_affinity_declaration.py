"""SOLID 是正 🔴-3: スレッド親和性の宣言化（call_binding._TABLE が唯一の真実源）。

HTTP 殻（framework/server.py）は指標名を知らず requires_dedicated_worker() を呼ぶだけ。
rpy2/R 依存の tgp_btlm のみ dedicated＝従来の名指し分岐と同一の振る舞いを固定する。
"""
from adapter.compute.call_binding import _TABLE, requires_dedicated_worker


def test_tgp_btlm_is_declared_dedicated():
    assert requires_dedicated_worker("tgp_btlm") is True


def test_all_other_indicators_use_the_pool():
    others = {cid for (cid, _v) in _TABLE if cid != "tgp_btlm"}
    assert others, "テーブルに他指標が存在する前提"
    for cid in others:
        assert requires_dedicated_worker(cid) is False, cid


def test_unknown_and_missing_ids_fall_back_to_pool():
    assert requires_dedicated_worker("no_such_indicator") is False
    assert requires_dedicated_worker(None) is False
    assert requires_dedicated_worker("") is False


def test_dedicated_declarations_match_legacy_hardcode():
    # 従来の殻ハードコード（== "tgp_btlm"）と宣言集合が完全一致＝振る舞い不変の回帰壁。
    dedicated = {cid for (cid, _v), spec in _TABLE.items()
                 if spec.get("thread_affinity") == "dedicated"}
    assert dedicated == {"tgp_btlm"}
