"""Latest 増分計算ディスパッチ（Stage A 基盤）— full / latest の上位入口。

純粋関数（各指標 core / add_*）は不変。本モジュールは /compute 境界で:
  full_compute   : 既存どおり adapter.compute(...) を全件で呼ぶ（後方互換の基準）。
  latest_compute : meta=latest_meta(...) を解決し、
                   archetype=="incremental" の宣言があれば増分計算（保持した状態を 1 点
                   進める・ISSUE-233）で末尾 K 点を得る。宣言が無い / 増分器が扱えない
                   パラメータでは、従来どおり df を min_window で tail、adapter.compute を
                   不変呼び出し、応答 series を末尾 K 点に切る。

full と latest は互いに import しない（_trail のみ latest 側に閉じる）。
"""

from __future__ import annotations

from typing import Any

from adapter.compute import incremental_state
from adapter.compute.fake_chart import TIMESERIES_KINDS
from adapter.compute.latest_meta import latest_meta

# 末尾K切りの対象 kind は kind の定義側（fake_chart.TIMESERIES_KINDS）が唯一源（ISSUE-278 #2）。
#   ここに写しを置くと kind 追加時に取り残され、その kind だけ全件が返る（無言の性能退行）。
_TRIMMABLE_KINDS = TIMESERIES_KINDS


def full_compute(
    adapter: Any, compute_id: str, variant: str, df: Any, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """既存どおり全件で adapter.compute(...) を呼ぶ（mode 省略=full の既定経路）。"""
    return adapter.compute(compute_id, variant, df, params)


def _trail(series: list[dict[str, Any]], k: int | None) -> list[dict[str, Any]]:
    """各 series の line/histogram data を末尾 K 点に切る（horizontal_line は不変）。

    k is None（axis_distribution）は series をそのまま返す（全件）。
    """
    if k is None:
        return series
    trimmed: list[dict[str, Any]] = []
    for s in series:
        if s.get("kind") in _TRIMMABLE_KINDS and "data" in s:
            trimmed.append({**s, "data": s["data"][-k:]})
        else:
            trimmed.append(s)
    return trimmed


def latest_compute(
    adapter: Any, compute_id: str, variant: str, df: Any, params: dict[str, Any],
    *, min_tail: "int | None" = None,
) -> list[dict[str, Any]]:
    """Latest（末尾K）計算: df を min_window で tail → 不変計算 → 末尾K切り。

    min_tail（ISSUE-162・additive）: 末尾切りの下限点数。形成中バー注入で欠落閉周期を
    合成した場合、合成バーぶん応答に含めないとクライアント側が歯抜けになるため、
    trailing_k との大きい方を採用する。None は従来どおり（後方互換）。
    """
    meta = latest_meta(compute_id, variant, params)
    k = meta.trailing_k
    if k is not None and min_tail is not None and min_tail > k:
        k = min_tail

    # 増分計算（ISSUE-233）: 宣言された指標は「保持した状態を 1 点進める」経路で末尾 K 点を
    #   得る（窓全体の再計算を行わない）。増分器が当該 (df, params) を扱えない場合は None が
    #   返り、下の従来経路へ落ちる＝未宣言指標・未対応パラメータの挙動は 1 ビットも変わらない。
    if meta.archetype == "incremental" and meta.incremental is not None:
        series = incremental_state.compute(
            adapter, compute_id, variant, df, params, name=meta.incremental, k=k
        )
        if series is not None:
            return series

    sub = df if meta.min_window is None else df.tail(meta.min_window)
    series = adapter.compute(compute_id, variant, sub, params)
    return _trail(series, k)
