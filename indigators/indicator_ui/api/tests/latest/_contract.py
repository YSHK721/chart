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


def assert_latest_non_empty(adapter: Any, compute_id: str, variant: str, df: Any, params: dict) -> None:
    """latest 経路がエラーなく走り、非空の payload list を返す。"""
    out = latest_compute(adapter, compute_id, variant, df, params)
    assert isinstance(out, list)
    assert len(out) > 0


def assert_series_kind_counts(
    adapter: Any, compute_id: str, variant: str, df: Any, params: dict, counts: "dict[str, int]"
) -> None:
    """full が返す系列の kind 別本数が catalog 定義と一致する。"""
    full = full_compute(adapter, compute_id, variant, df, params)
    for kind, expected in counts.items():
        assert len(by_kind(full, kind)) == expected


def assert_trimmable_tail_matches(
    adapter: Any, compute_id: str, variant: str, df: Any, params: dict
) -> None:
    """line/histogram 各系列で latest の ``data[-K:]`` が full の ``data[-K:]`` と完全一致する。"""
    meta = latest_meta(compute_id, variant, params)
    k = meta.trailing_k if meta.trailing_k is not None else 1

    full = full_compute(adapter, compute_id, variant, df, params)
    latest = latest_compute(adapter, compute_id, variant, df, params)
    full_tr = {p["name"]: p for p in by_kind(full, "line") + by_kind(full, "histogram")}
    latest_tr = {p["name"]: p for p in by_kind(latest, "line") + by_kind(latest, "histogram")}

    assert set(full_tr) == set(latest_tr)
    assert latest_tr, "trimmable series should not be empty"
    for name, fp in full_tr.items():
        lp = latest_tr[name]
        assert len(lp["data"]) <= k          # latest 各系列は末尾 K 点に切られている
        f_tail, l_tail = fp["data"][-k:], lp["data"][-k:]
        assert len(l_tail) == len(f_tail)
        for fpt, lpt in zip(f_tail, l_tail):
            assert fpt["time"] == lpt["time"]
            assert fpt["value"] == lpt["value"]


def assert_horizontal_line_levels_match(
    adapter: Any, compute_id: str, variant: str, df: Any, params: dict, *, level_count: int
) -> None:
    """horizontal_line が latest でも full と同一（末尾切りされない）で、水準が完全一致する。"""
    full = full_compute(adapter, compute_id, variant, df, params)
    latest = latest_compute(adapter, compute_id, variant, df, params)
    full_hl = by_kind(full, "horizontal_line")
    latest_hl = by_kind(latest, "horizontal_line")

    assert len(full_hl) == len(latest_hl) == 1
    f_lines, l_lines = full_hl[0]["lines"], latest_hl[0]["lines"]
    assert len(l_lines) == len(f_lines) == level_count
    for fl, ll in zip(f_lines, l_lines):
        assert fl["price"] == ll["price"]    # float 完全一致
        assert fl["text"] == ll["text"]


def assert_safe_default_meta(
    compute_id: str, variants: Iterable[str], params_of: Callable[[], dict]
) -> None:
    """latest_meta 未登録 → 安全既定 ``("recurrence", None, 1)``（full＋K=1）へ解決される。"""
    for variant in variants:
        meta = latest_meta(compute_id, variant, dict(params_of()))
        assert meta.archetype == "recurrence"
        assert meta.min_window is None  # full（tail せず全件）
        assert meta.trailing_k == 1
