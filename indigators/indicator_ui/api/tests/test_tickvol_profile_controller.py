"""tickvol_profile_controller（/tickvol_profile の純ロジック）の検証。

取引密度ハイライト（1 時間足以下でチャートパネル背景色を変える帯）の唯一の実装。リプレイ core は
本 handler を bridge 経由で read-only 再利用するため、ここが両 UI の帯定義を決める。
"""
from __future__ import annotations

import pandas as pd
import pytest

from adapter.controller import tickvol_profile_controller as tc
from usecase.serve_tickvol_profile import (
    TickvolProfileRequest,
    serve_tickvol_profile,
)


class _Port:
    """DatasetPort の最小 fake（ホワイトリスト検証＋1 分足 DataFrame 供給）。"""

    def __init__(self, df=None, known=("jp225_tick",)):
        self.df = df
        self.known = known
        self.calls = []

    def is_known(self, ref):
        return ref in self.known

    def is_known_timeframe(self, tf):
        return tf in ("1m", "5m", "1h", "1D")

    def load_dataframe(self, ref, timeframe):
        self.calls.append((ref, timeframe))
        if self.df is None:
            raise RuntimeError("dataset failure")
        return self.df


def _df(rows):
    idx = pd.to_datetime([t for t, _ in rows], unit="s")
    return pd.DataFrame({"volume": [v for _, v in rows]}, index=idx)


def test_unknown_ref_is_validation_400():
    st, body = tc.handle_tickvol_profile("nope")
    assert st == 400
    assert body["ok"] is False
    assert body["error"]["type"] == "validation"


def test_dataset_failure_is_internal_500():
    result = serve_tickvol_profile(
        TickvolProfileRequest(dataset_ref="jp225_tick"),
        dataset_port=_Port(df=None),
        profile=tc.tickvol_profile_mod,
    )
    assert result.error_type == "internal"


def test_aggregation_uses_the_1m_atom_regardless_of_display_timeframe():
    # 帯は「市場の性質」であって「チャートの拡大率」の関数ではない＝集計は常に 1 分足原子。
    port = _Port(df=_df([(1785531600 + 60 * i, 1.0) for i in range(120)]))
    serve_tickvol_profile(
        TickvolProfileRequest(dataset_ref="jp225_tick"),
        dataset_port=port,
        profile=tc.tickvol_profile_mod,
    )
    assert port.calls == [("jp225_tick", "1m")]


def test_response_shape_carries_bands_bins_and_threshold():
    # 1 セッションだけ・ビン 0 と 1 に差をつける（帯が 1 本出る最小構成）。
    rows = [(1785531600 + 60 * i, 100.0 if i < 15 else 1.0) for i in range(60)]
    result = serve_tickvol_profile(
        TickvolProfileRequest(dataset_ref="jp225_tick"),
        dataset_port=_Port(df=_df(rows)),
        profile=tc.tickvol_profile_mod,
    )
    assert result.bin_sec == 900
    assert result.sessions == 1
    assert result.bands == [{"startOff": 0, "endOff": 900}]
    assert result.bins[0] == {"off": 0, "value": 1500.0}   # 15 本 × 100
    assert result.threshold > 0


@pytest.mark.parametrize(
    "raw,expected", [("20", 20), ("", None), (None, None), ("abc", None), ("-5", -5)]
)
def test_query_values_are_parsed_by_the_controller(raw, expected):
    assert tc._int_or_none(raw) == expected


def test_out_of_range_params_are_clamped_not_rejected():
    # ノブの誤入力で「黙って精度が落ちる」ことを防ぐ（N は 1m tail の制約から上限 25）。
    port = _Port(df=_df([(1785531600 + 60 * i, 1.0) for i in range(60)]))
    result = serve_tickvol_profile(
        TickvolProfileRequest(dataset_ref="jp225_tick", sessions=999, pct=999),
        dataset_port=port,
        profile=tc.tickvol_profile_mod,
    )
    assert result.ok  # 400 にはしない（帯が出ないだけの縮退は避ける）
