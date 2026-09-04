"""variant ごとの受理 param 集合（paramScopes）— 計算へ送る前に params を絞る唯一の場所。

なぜ要るか（ISSUE-466・実測 2026-08-30）:
    テンプレートの instance は UI 側パラメータ（`wait_for_close`）や、指標から外された
    廃止パラメータ（profit_rsi の `ma_period`）を運ぶ。ライブ core は ISSUE-278 #8 以降
    **受理しない param を無言で捨てず validation エラー**にするため、素通しすると当該
    instance が丸ごと供給失敗になり、実テンプレートでは MA 全 8 足 × 3 本と profit_rsi が
    縮退掲示へ落ちた。

単一ソース:
    受理集合の正はライブ core の ``catalog_param_scopes()``（指標記述子 ``call_binding._TABLE``
    の ``params_defaults`` からの導出値）であり、写しを作らない。``GET /catalog`` が front へ
    配るものと同一物を bridge 経由で読む。

参照実装:
    ライブ UI ``usecase/catalog.js`` の ``scopedParams`` と
    ``tools/measure/issue449/probe_inverse.py:67``。どちらも**許可リスト**であり、受理集合に
    無いキーは黙って落とす。受理集合そのものを持たない指標 / variant は素通しする
    （知らない相手を勝手に絞ると、供給失敗の理由が「受理されない param」から
    「そもそも呼ばれなかった」へすり替わって原因が消える）。

計算量（CLAUDE.md §4.1・ISSUE-464 と同型の無駄を作らない）:
    受理集合はプロセスの中で変わらない定数である。**発行のたびに解決しない**。保持の寿命を
    決めるのは Composition Root であり（素材ストアと同じ規律）、この型は「1 回だけ解決する」
    ことだけを保証する。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

#: 受理集合の形（compute_id → variant → 受理 param 名）。
ParamScopeTable = "Mapping[str, Mapping[str, object]]"


def _bridge_source() -> "Mapping[str, Mapping[str, object]]":
    """既定の取得元（ライブ core の単一ソースを bridge 越しに読むだけ）。"""
    from indigators.indicator_ui import api_loader  # 遅延: 技術隔離

    return scopes_of(api_loader.load_compute())


def scopes_of(bridge: Any) -> "Mapping[str, Mapping[str, object]]":
    """bridge namespace から受理集合を読む（公開していない bridge では空＝素通し）。

    `compute_error` と同じ加法公開の前例に従う。公開していない bridge（テストダブル・
    旧版）では絞り込みを行わず従来どおり素通しする。
    """
    source = getattr(bridge, "catalog_param_scopes", None)
    return dict(source() if callable(source) else {})


class ParamScopes:
    """受理集合を 1 回だけ解決し、params をその集合へ絞る。

    Args:
        source: 受理集合の取得手順（省略時はライブ core の単一ソースを bridge 越しに読む）。
            **この手順は高々 1 回しか呼ばれない**。
    """

    def __init__(
        self, *, source: "Callable[[], Mapping[str, Mapping[str, object]]] | None" = None
    ) -> None:
        self._source = source if source is not None else _bridge_source
        self._table: "Mapping[str, Mapping[str, object]] | None" = None

    def scoped(
        self, *, indicator_id: str, variant: str, params: "Mapping[str, object]"
    ) -> "dict[str, object]":
        """その variant が受理するキーだけを残した params。

        受理集合を持たない指標 / variant は素通しする（参照実装と同じ）。
        """
        accepted = (self._resolved().get(indicator_id) or {}).get(variant)
        if accepted is None:
            return dict(params)
        allowed = frozenset(accepted)
        return {name: value for name, value in params.items() if name in allowed}

    # ------------------------------------------------------------------ 内部
    def _resolved(self) -> "Mapping[str, Mapping[str, object]]":
        if self._table is None:
            self._table = dict(self._source())
        return self._table
