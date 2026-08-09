"""Latest 増分計算フレームワークの**契約アサーション**（ISSUE-310）。

なぜ 1 箇所に置くか:
    ``latest/`` の各 test_*_latest.py は指標ごとに独立したファイルだが、検証している契約は
    共通である（latest の trimmable 系列は full の末尾 K 点と float 完全一致する／
    horizontal_line は切らない／未登録 meta は安全既定 recurrence/full/K=1）。実測では
    **本体が 1 文字も違わない検定が 9 群・25 箇所**あった（codescan の削減見込み上位）。
    契約が変わったとき 25 箇所を直すのは事故のもとなので、契約そのものを 1 つにする。

指標ごとに違うもの（＝各ファイルが持ち続けるもの）:
    ``_COMPUTE_ID`` / ``_VARIANTS`` / ``_TRIMMABLE`` / ``_ohlcv()`` / ``_params()``。
    とくに ``_ohlcv`` は 23 ファイルすべて中身が異なる（その指標を意味のある値域で動かす
    合成データ＝検証対象そのもの）ため、共通化しない。

呼び出し規約: 本モジュールの関数は「契約」だけを持ち、データも既定値も持たない。
    必要な値はすべて引数で受ける（暗黙の既定を作らない＝どの指標で何を検証したかが呼出側で読める）。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from adapter.compute import IndicatorComputeAdapter
from adapter.compute.latest_dispatch import full_compute, latest_compute
from adapter.compute.latest_meta import latest_meta


def by_kind(payloads: "list[dict]", kind: str) -> "list[dict]":
    """系列 payload を kind で絞る。"""
    return [p for p in payloads if p.get("kind") == kind]


def assert_horizontal_lines_untrimmed(
    compute_id: str, variants: Iterable[str], df: Any, params_of: Callable[[], dict]
) -> None:
    """horizontal_line は末尾 K 切りの対象外＝full と系列ごとに完全一致する。"""
    adapter = IndicatorComputeAdapter()
    for variant in variants:
        params = params_of()
        full = full_compute(adapter, compute_id, variant, df, dict(params))
        latest = latest_compute(adapter, compute_id, variant, df, dict(params))
        full_by_name = {s["name"]: s for s in full}

        hlines = by_kind(latest, "horizontal_line")
        assert hlines, "expected horizontal_line series"
        for s in hlines:
            # horizontal_line は data を持たず lines を持つ → full と完全一致（切らない）。
            assert s == full_by_name[s["name"]]


def assert_horizontal_lines_identical_to_full(
    compute_id: str, variants: Iterable[str], df: Any, params_of: Callable[[], dict]
) -> None:
    """horizontal_line 群が latest と full で**並びごと**同一であること。"""
    adapter = IndicatorComputeAdapter()
    for variant in variants:
        params = params_of()
        full = full_compute(adapter, compute_id, variant, df, dict(params))
        latest = latest_compute(adapter, compute_id, variant, df, dict(params))
        latest_hl = by_kind(latest, "horizontal_line")
        assert latest_hl, "horizontal_line series should be present"
        assert latest_hl == by_kind(full, "horizontal_line")


def assert_tail_matches_full(
    compute_id: str,
    variants: Iterable[str],
    df: Any,
    params_of: Callable[[], dict],
    *,
    kinds: Iterable[str] = ("histogram",),
    expect_k: "int | None" = None,
) -> None:
    """**最重要の契約**: latest の ``data[-K:]`` が full の ``data[-K:]`` と float 完全一致する。

    Args:
        kinds: 末尾 K 切りの対象となる系列 kind（既定は histogram のみ）。
        expect_k: 期待する ``trailing_k``。指定時は meta の K がその値であることも固定する。
    """
    adapter = IndicatorComputeAdapter()
    kinds = tuple(kinds)
    for variant in variants:
        params = params_of()
        k = latest_meta(compute_id, variant, dict(params)).trailing_k
        if expect_k is not None:
            assert k == expect_k
        full = full_compute(adapter, compute_id, variant, df, dict(params))
        latest = latest_compute(adapter, compute_id, variant, df, dict(params))
        full_by_name = {s["name"]: s for s in full}

        trimmable = [s for s in latest if s["kind"] in kinds]
        assert trimmable, f"expected at least one {'/'.join(kinds)} series"
        for s in trimmable:
            f = full_by_name[s["name"]]
            assert len(s["data"]) <= k  # 末尾 K 点に切られている
            assert s["data"] == f["data"][-k:]  # time/value とも完全一致


def assert_latest_returns_kinds(
    compute_id: str,
    variants: Iterable[str],
    df: Any,
    params_of: Callable[[], dict],
    *,
    required: "set[str] | None" = None,
    exact: "set[str] | None" = None,
) -> None:
    """latest 経路がエラーなく走り、期待する kind の系列を返す。

    ``required`` は「少なくともこれらを含む」、``exact`` は「ちょうどこの集合」。
    どちらを課すかは指標の catalog 定義に依るため、呼出側が明示する。
    """
    adapter = IndicatorComputeAdapter()
    for variant in variants:
        latest = latest_compute(adapter, compute_id, variant, df, dict(params_of()))
        assert latest, "latest series should not be empty"
        kinds = {s["kind"] for s in latest}
        if exact is not None:
            assert kinds == exact
        if required is not None:
            assert required <= kinds


def assert_safe_default_meta(
    compute_id: str, variants: Iterable[str], params_of: Callable[[], dict]
) -> None:
    """latest_meta 未登録 → 安全既定 ``("recurrence", None, 1)``（full＋K=1）へ解決される。"""
    for variant in variants:
        meta = latest_meta(compute_id, variant, dict(params_of()))
        assert meta.archetype == "recurrence"
        assert meta.min_window is None  # full（tail せず全件）
        assert meta.trailing_k == 1
