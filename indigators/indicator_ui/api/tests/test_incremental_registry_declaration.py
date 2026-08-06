"""増分器レジストリの宣言を **検定で強制する**（ISSUE-262）。

``adapter/compute/incremental/__init__.py`` の docstring は
「増分器の追加は ``_FACTORIES`` へ 1 行足すだけ（OCP）」と宣言している。しかし実際には
``call_binding._TABLE`` 側の ``latest_meta`` 第 4 要素（増分器名）も併せて宣言しなければ
``resolve()`` が ``None`` を返し、**例外を出さずに従来の重い経路へ黙って縮退**する。
すなわち片方だけの追加・rename は性能退行として無言で現れ、テストは緑のままだった。

本テストは 2 つの宣言の集合一致を固定し、片方だけの更新を落とす。
"""
from __future__ import annotations

import pytest

from adapter.compute import call_binding as _cb
from adapter.compute import incremental as _inc


def _declared_incrementer_names() -> "set[str]":
    """``_TABLE`` の ``latest_meta`` が参照している増分器名の集合。

    ``latest_meta`` は ``LatestMeta`` を返す callable（ISSUE-278 #7 で位置タプルを廃止）。
    params 依存で分岐する指標があるため、既定パラメータで 1 回評価して名前を採る。
    以前は ``len(resolved) >= 4`` という要素数ヒューリスティックで判定しており、増分器名を
    書き忘れた 3 要素宣言を「宣言なし」と解釈して見逃していた。属性で読めば取り違えない。
    """
    names: "set[str]" = set()
    for (compute_id, _variant), spec in _cb._TABLE.items():
        meta = spec.get("latest_meta")
        if meta is None:
            continue
        try:
            resolved = meta(spec.get("params_defaults") or {})
        except Exception:  # noqa: BLE001 — 評価不能な宣言は本テストの対象外
            continue
        if resolved is None or resolved.archetype != "incremental":
            continue
        if isinstance(resolved.incremental, str):
            names.add(resolved.incremental)
    return names


def test_declarations_return_typed_meta():
    """宣言は位置タプルでなく ``LatestMeta`` を返す（ISSUE-278 #7）。

    タプル宣言だと 4 要素目（増分器名）の書き忘れが型検査を通り、実行時は例外なく full
    再計算へ縮退する（値は正しいまま性能だけ落ちるので検定も緑）。型で塞ぐ。
    """
    from adapter.compute.latest_meta_spec import LatestMeta

    for (compute_id, variant), spec in _cb._TABLE.items():
        meta = spec.get("latest_meta")
        if meta is None:
            continue
        resolved = meta(spec.get("params_defaults") or {})
        assert isinstance(resolved, LatestMeta), (
            f"{compute_id}/{variant} の latest_meta が LatestMeta を返していません: {type(resolved)}"
        )


def test_every_declared_incrementer_name_resolves_to_a_factory():
    """``latest_meta`` が名指しした増分器は必ず ``_FACTORIES`` に在る。

    落ちる = call_binding 側だけを更新した状態。``resolve()`` が None を返し、指標は例外なく
    従来の重い経路へ縮退する（無言の性能退行）。``_FACTORIES`` へ追加すること。
    """
    missing = _declared_incrementer_names() - set(_inc._FACTORIES)
    assert not missing, (
        f"call_binding が名指しした増分器が _FACTORIES に在りません: {sorted(missing)}。"
        " 片方だけの追加は無言で従来経路へ縮退します。"
    )


def test_every_factory_is_referenced_by_a_declaration():
    """``_FACTORIES`` の全エントリが ``latest_meta`` から参照されている（死に登録を作らない）。"""
    unused = set(_inc._FACTORIES) - _declared_incrementer_names()
    assert not unused, (
        f"_FACTORIES に、どの指標からも参照されない登録が残っています: {sorted(unused)}。"
        " call_binding 側の latest_meta 宣言を足すか、登録を撤去してください。"
    )


@pytest.mark.parametrize("name", sorted(_inc._FACTORIES))
def test_each_registered_factory_actually_builds(name):
    """登録された factory が実際に増分器を生成できる（遅延 import の壊れを起動前に落とす）。"""
    assert _inc.resolve(name) is not None, f"{name} の増分器を解決できません"
