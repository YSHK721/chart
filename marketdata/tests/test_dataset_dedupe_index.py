"""marketdata.dataset の serving hygiene 漏斗（_clamp_outlier_bars）が重複 time を無害化する検証。

ISSUE-167: 素材 CSV（jp225_tick_m1）に日境界の二重分バーが残っていても、serving でチャート/指標へ
渡る前に同一 time を keep-last で畳み「厳密増加 time」を保証する（フロント lwc の不変条件違反＝
毎フレーム "Value is null" クラッシュを遮断）。全 ref・全返却経路が通る単一漏斗で適用される。
"""

from __future__ import annotations

import pandas as pd

from marketdata import dataset


def _df(idx_iso: list[str], closes: list[float]) -> pd.DataFrame:
    idx = pd.to_datetime(idx_iso)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes}, index=idx
    )


def test_clamp_outlier_bars_dedupes_duplicate_index_keep_last() -> None:
    # 非市場 ref（clamp 対象外）でも dedupe は効く＝全 ref 一様。
    df = _df(
        ["2026-07-23 23:58:00", "2026-07-23 23:59:00", "2026-07-23 23:59:00"],
        [1.0, 2.0, 20.0],
    )
    out = dataset._clamp_outlier_bars(df, "unknown_ref_not_clamped")
    assert not out.index.has_duplicates
    assert len(out) == 2
    assert out.iloc[-1]["close"] == 20.0  # keep-last（後勝ち）


def test_clamp_outlier_bars_noop_on_unique_index() -> None:
    df = _df(["2026-07-23 23:58:00", "2026-07-23 23:59:00"], [1.0, 2.0])
    out = dataset._clamp_outlier_bars(df, "unknown_ref_not_clamped")
    assert list(out["close"]) == [1.0, 2.0]  # 挙動不変
