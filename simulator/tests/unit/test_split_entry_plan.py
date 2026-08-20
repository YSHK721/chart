"""split_entry_plan（Step 3 分割ロット変換）の権威検定（ISSUE-368 スライス 0）。

設計入力（唯一の仕様源）: `.doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md` 出力 3 スライス 0。
権威: `simulator/usecase/split_entry_plan.py`。
正解の定義（参照実装）: `integrated_position_sizing_calculator.html` を **pmode='direct'** に
切り替えた状態（TBD-1 裁定＝建値は価格の単一ソース。gap モードは移植対象外）。

期待値の算出根拠（推測ではなく実測。2026-08-20）:
    参照実装 HTML の当該 JS を**そのまま実行**して得た出力を literal として固定した。
    実行方法（再現手順）: HTML を 1 byte も変更せず読み出し、以下の行範囲を連結して
    `new Function('S','num','chosenF', src)` に載せ、`build('up')` を呼ぶ。
        :875-888  ensureCustomLen / genWeights
        :902-913  ensureCustomGLen / offsetsFrom（direct では戻り値未使用。実行可能にするためだけに同梱）
        :928-933  ensureCustomPLen
        :948-955  effectiveD / effectiveTP
        :956-1031 build
    スタブは `num(id)`（入力欄の値）・`S`（画面状態）・`chosenF()`（採用 f）の 3 つのみで、
    式・分岐には一切手を入れていない。S は HTML :578 の既定に `pmode:'direct'` を重ねた状態
    （既定は smode:'price' / ltmode:'lc' / lotmode:'int' / wpattern:'linear' / tpmode:'dist'）。
    入力既定値は HTML :278-289, :355-362, :415, :463-466, :484-487 のとおり
    （p=38%, R=2.74, E=172000, V=1, P₀=58700, mr=10%, stop=58340, g=1000, linear）。
    建値列は direct 指定＝HTML :931 の既定シード `P₀ + i·g` を明示入力した値。

浮動小数の許容差（実測に基づく・無検証で緩めない）:
    `losscut_price` / `losscut_distance` は権威 `account_engine.official_losscut_price` を
    **呼んで**求める（複製禁止・`sizing_ports.py:42-52`）。参照実装 HTML は代数的に等価だが
    結合順の異なる式 `avgP − (E−reqMargin)/U` を使うため最終桁が一致しない。
    実測（17 ケース）: losscut_price は相対 ≤ 2.44e-16（1〜2 ULP）、losscut_distance は
    差の相殺により相対 ≤ 1.04e-14（絶対 ≤ 1e-11 pt）。
    `cap_basis='lc'` のときは上限を閉形式で書き下さず二分探索で権威関数に判定させるため、
    `cap_lot` とその伝播先（`scale` / `buildable_lot` / `effective_risk`）にも同じ桁の差が乗る。
    よって上記の項目のみ相対許容 1e-13 とし、**他の全項目は厳密一致（許容 0）**とする。
    許容は実測値より 1 桁以上厳しく設定してあり、`buildable_lot` の整数丸め（floor）が
    1 単位ずれれば相対 1e-2 以上となって検出される。
"""
from __future__ import annotations

import math

import pytest

from simulator.usecase.split_entry_plan import (
    SplitEntrySpec, build_split_entry_plan, generate_weights,
)

# 権威 official_losscut_price 経由で ULP 差が入りうる項目（下記 docstring の実測に基づく）。
# これ以外は**厳密一致（許容 0）**で突き合わせる。
_TOLERANT_ALWAYS = {"losscut_price", "losscut_distance"}
# cap_basis='lc' のときだけ二分探索（cap_lot）を経由して伝播する項目。
_TOLERANT_LC_ONLY = {"cap_lot", "scale", "buildable_lot", "effective_risk"}
_REL_TOL = 1e-13


def _plan(case: dict):
    return build_split_entry_plan(SplitEntrySpec(**case["params"]))


def _check(plan, case: dict, keys) -> None:
    tolerant = set(_TOLERANT_ALWAYS)
    if case["params"]["cap_basis"] == "lc":
        tolerant |= _TOLERANT_LC_ONLY
    for key in keys:
        if key not in case["expected"]:
            continue
        want = case["expected"][key]
        got = getattr(plan, key)
        if isinstance(want, list):
            got = list(got)
            assert len(got) == len(want), f"{key}: 要素数"
            for i, (g, w) in enumerate(zip(got, want)):
                assert g == w, f"{key}[{i}]: {g!r} != {w!r}"
        elif (key in tolerant and isinstance(want, (int, float))
                and not isinstance(want, bool) and math.isfinite(want)):
            assert got == pytest.approx(want, rel=_REL_TOL, abs=0.0), f"{key}: {got!r} != {want!r}"
        else:
            assert got == want, f"{key}: {got!r} != {want!r}"


GOLDEN = {
    "default_K1": dict(
        params=dict(direction='long', entry_prices=[58700],
                    stop_price=58340, take_price=None,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.07686131386861314, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360],
            "entry_prices": [58700],
            "weights": [1],
            "weighted_distance_sum": 360,
            "base_lot": 36.722627737226276,
            "lots_raw": [36.722627737226276],
            "lots": [36],
            "total_lot": 36,
            "avg_price": 58700,
            "risk_shares": [1],
            "total_risk": 12960,
            "loss_rate": 0.07534883720930233,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 211320,
            "margin_use": 1.2286046511627906,
            "losscut_price": 59792.22222222222,
            "losscut_distance": -1092.2222222222222,
            "stop_distance": 360,
            "lc_before_stop": True,
            "immediate_lc": True,
            "cap_target": 58340,
            "cap_lot": 27.608346709470272,
            "scale": 0.7668985197075076,
            "buildable_lot": 27,
            "effective_risk": 9720,
            "margin_binds": True,
        },
    ),
    "default_K2": dict(
        params=dict(direction='long', entry_prices=[58700, 59700],
                    stop_price=58340, take_price=None,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.07686131386861314, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360, 1360],
            "entry_prices": [58700, 59700],
            "weights": [1, 2],
            "weighted_distance_sum": 3080,
            "base_lot": 4.292255190065409,
            "lots_raw": [4.292255190065409, 8.584510380130817],
            "lots": [4, 8],
            "total_lot": 12,
            "avg_price": 59366.666666666664,
            "risk_shares": [0.11688311688311688, 0.8831168831168831],
            "total_risk": 12320,
            "loss_rate": 0.07162790697674419,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 71240,
            "margin_use": 0.4141860465116279,
            "losscut_price": 50970,
            "losscut_distance": 8396.666666666666,
            "stop_distance": 1026.6666666666642,
            "lc_before_stop": False,
            "immediate_lc": False,
            "cap_target": 58340,
            "cap_lot": 24.70081378650071,
            "scale": 1,
            "buildable_lot": 12,
            "effective_risk": 12320,
            "margin_binds": False,
        },
    ),
    "default_K3": dict(
        params=dict(direction='long', entry_prices=[58700, 59700, 60700],
                    stop_price=58340, take_price=None,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.07686131386861314, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360, 1360, 2360],
            "entry_prices": [58700, 59700, 60700],
            "weights": [1, 2, 3],
            "weighted_distance_sum": 10160,
            "base_lot": 1.3011954710040807,
            "lots_raw": [1.3011954710040807, 2.6023909420081615, 3.903586413012242],
            "lots": [1, 2, 3],
            "total_lot": 6,
            "avg_price": 60033.333333333336,
            "risk_shares": [0.03543307086614173, 0.2677165354330709, 0.6968503937007874],
            "total_risk": 10160,
            "loss_rate": 0.05906976744186047,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 36020,
            "margin_use": 0.2094186046511628,
            "losscut_price": 37370,
            "losscut_distance": 22663.333333333332,
            "stop_distance": 1693.3333333333358,
            "lc_before_stop": False,
            "immediate_lc": False,
            "cap_target": 58340,
            "cap_lot": 22.347336509311376,
            "scale": 1,
            "buildable_lot": 6,
            "effective_risk": 10160,
            "margin_binds": False,
        },
    ),
    "K3_take_bracket": dict(
        params=dict(direction='long', entry_prices=[58700, 59700, 60700],
                    stop_price=58340, take_price=61500,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.07686131386861314, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360, 1360, 2360],
            "entry_prices": [58700, 59700, 60700],
            "weights": [1, 2, 3],
            "weighted_distance_sum": 10160,
            "base_lot": 1.3011954710040807,
            "lots_raw": [1.3011954710040807, 2.6023909420081615, 3.903586413012242],
            "lots": [1, 2, 3],
            "total_lot": 6,
            "avg_price": 60033.333333333336,
            "risk_shares": [0.03543307086614173, 0.2677165354330709, 0.6968503937007874],
            "total_risk": 10160,
            "loss_rate": 0.05906976744186047,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": 0.8661417322834646,
            "profit_yen": 8800,
            "profit_rate": 0.05116279069767442,
            "breakeven": 0.5358649789029536,
            "excess": -0.1558649789029536,
            "ev_yen": -2955.2,
            "win_rate": 0.38,
            "required_margin": 36020,
            "margin_use": 0.2094186046511628,
            "losscut_price": 37370,
            "losscut_distance": 22663.333333333332,
            "stop_distance": 1693.3333333333358,
            "lc_before_stop": False,
            "immediate_lc": False,
            "cap_target": 58340,
            "cap_lot": 22.347336509311376,
            "scale": 1,
            "buildable_lot": 6,
            "effective_risk": 10160,
            "margin_binds": False,
        },
    ),
    "K3_dec_lots": dict(
        params=dict(direction='long', entry_prices=[58700, 59700, 60700],
                    stop_price=58340, take_price=61500,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.07686131386861314, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='dec',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360, 1360, 2360],
            "entry_prices": [58700, 59700, 60700],
            "weights": [1, 2, 3],
            "weighted_distance_sum": 10160,
            "base_lot": 1.3011954710040807,
            "lots_raw": [1.3011954710040807, 2.6023909420081615, 3.903586413012242],
            "lots": [1.3011954710040807, 2.6023909420081615, 3.903586413012242],
            "total_lot": 7.807172826024484,
            "avg_price": 60033.333333333336,
            "risk_shares": [0.03543307086614173, 0.2677165354330709, 0.6968503937007874],
            "total_risk": 13220.14598540146,
            "loss_rate": 0.07686131386861314,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": 0.8661417322834647,
            "profit_yen": 11450.520144835911,
            "profit_rate": 0.06657279153974367,
            "breakeven": 0.5358649789029535,
            "excess": -0.15586497890295348,
            "ev_yen": -3845.292855911258,
            "win_rate": 0.38,
            "required_margin": 46869.060865566986,
            "margin_use": 0.27249453991608713,
            "losscut_price": 44005.644191199746,
            "losscut_distance": 16027.689142133588,
            "stop_distance": 1693.3333333333358,
            "lc_before_stop": False,
            "immediate_lc": False,
            "cap_target": 58340,
            "cap_lot": 22.347336509311376,
            "scale": 1,
            "buildable_lot": 7.807172826024484,
            "effective_risk": 13220.14598540146,
            "margin_binds": False,
        },
    ),
    "K3_margin_basis": dict(
        params=dict(direction='long', entry_prices=[58700, 59700, 60700],
                    stop_price=58340, take_price=None,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.07686131386861314, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='margin', win_rate=0.38),
        expected={
            "distances": [360, 1360, 2360],
            "entry_prices": [58700, 59700, 60700],
            "weights": [1, 2, 3],
            "weighted_distance_sum": 10160,
            "base_lot": 1.3011954710040807,
            "lots_raw": [1.3011954710040807, 2.6023909420081615, 3.903586413012242],
            "lots": [1, 2, 3],
            "total_lot": 6,
            "avg_price": 60033.333333333336,
            "risk_shares": [0.03543307086614173, 0.2677165354330709, 0.6968503937007874],
            "total_risk": 10160,
            "loss_rate": 0.05906976744186047,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 36020,
            "margin_use": 0.2094186046511628,
            "losscut_price": 37370,
            "losscut_distance": 22663.333333333332,
            "stop_distance": 1693.3333333333358,
            "lc_before_stop": False,
            "immediate_lc": False,
            "cap_target": None,
            "cap_lot": 28.650749583564686,
            "scale": 1,
            "buildable_lot": 6,
            "effective_risk": 10160,
            "margin_binds": False,
        },
    ),
    "K3_short": dict(
        params=dict(direction='short', entry_prices=[58700, 57700, 56700],
                    stop_price=59060, take_price=55900,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.07686131386861314, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360, 1360, 2360],
            "entry_prices": [58700, 57700, 56700],
            "weights": [1, 2, 3],
            "weighted_distance_sum": 10160,
            "base_lot": 1.3011954710040807,
            "lots_raw": [1.3011954710040807, 2.6023909420081615, 3.903586413012242],
            "lots": [1, 2, 3],
            "total_lot": 6,
            "avg_price": 57366.666666666664,
            "risk_shares": [0.03543307086614173, 0.2677165354330709, 0.6968503937007874],
            "total_risk": 10160,
            "loss_rate": 0.05906976744186047,
            "stop_price": 59060,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": 0.8661417322834646,
            "profit_yen": 8800,
            "profit_rate": 0.05116279069767442,
            "breakeven": 0.5358649789029536,
            "excess": -0.1558649789029536,
            "ev_yen": -2955.2,
            "win_rate": 0.38,
            "required_margin": 34420,
            "margin_use": 0.20011627906976745,
            "losscut_price": 80296.66666666666,
            "losscut_distance": 22930,
            "stop_distance": 1693.3333333333358,
            "lc_before_stop": False,
            "immediate_lc": False,
            "cap_target": 59060,
            "cap_lot": 23.149394347240914,
            "scale": 1,
            "buildable_lot": 6,
            "effective_risk": 10160,
            "margin_binds": False,
        },
    ),
    "K3_equal_weights": dict(
        params=dict(direction='long', entry_prices=[58700, 59700, 60700],
                    stop_price=58340, take_price=None,
                    weight_pattern='equal', custom_weights=None,
                    fraction=0.07686131386861314, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360, 1360, 2360],
            "entry_prices": [58700, 59700, 60700],
            "weights": [1, 1, 1],
            "weighted_distance_sum": 4080,
            "base_lot": 3.2402318591670243,
            "lots_raw": [3.2402318591670243, 3.2402318591670243, 3.2402318591670243],
            "lots": [3, 3, 3],
            "total_lot": 9,
            "avg_price": 59700,
            "risk_shares": [0.08823529411764706, 0.3333333333333333, 0.5784313725490197],
            "total_risk": 12240,
            "loss_rate": 0.07116279069767442,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 53730,
            "margin_use": 0.31238372093023253,
            "losscut_price": 46558.88888888889,
            "losscut_distance": 13141.111111111111,
            "stop_distance": 1360,
            "lc_before_stop": False,
            "immediate_lc": False,
            "cap_target": 58340,
            "cap_lot": 23.465211459754435,
            "scale": 1,
            "buildable_lot": 9,
            "effective_risk": 12240,
            "margin_binds": False,
        },
    ),
    "K3_double_weights": dict(
        params=dict(direction='long', entry_prices=[58700, 59700, 60700],
                    stop_price=58340, take_price=None,
                    weight_pattern='double', custom_weights=None,
                    fraction=0.07686131386861314, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360, 1360, 2360],
            "entry_prices": [58700, 59700, 60700],
            "weights": [1, 2, 4],
            "weighted_distance_sum": 12520,
            "base_lot": 1.0559222033068258,
            "lots_raw": [1.0559222033068258, 2.1118444066136517, 4.223688813227303],
            "lots": [1, 2, 4],
            "total_lot": 7,
            "avg_price": 60128.57142857143,
            "risk_shares": [0.02875399361022364, 0.21725239616613418, 0.7539936102236422],
            "total_risk": 12520,
            "loss_rate": 0.0727906976744186,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 42090,
            "margin_use": 0.24470930232558138,
            "losscut_price": 41570,
            "losscut_distance": 18558.571428571428,
            "stop_distance": 1788.5714285714275,
            "lc_before_stop": False,
            "immediate_lc": False,
            "cap_target": 58340,
            "cap_lot": 22.047244094488164,
            "scale": 1,
            "buildable_lot": 7,
            "effective_risk": 12520,
            "margin_binds": False,
        },
    ),
    "K3_custom_weights": dict(
        params=dict(direction='long', entry_prices=[58700, 59700, 60700],
                    stop_price=58340, take_price=None,
                    weight_pattern='custom', custom_weights=[3, 1, 2],
                    fraction=0.07686131386861314, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360, 1360, 2360],
            "entry_prices": [58700, 59700, 60700],
            "weights": [3, 1, 2],
            "weighted_distance_sum": 7160,
            "base_lot": 1.8463891041063492,
            "lots_raw": [5.539167312319048, 1.8463891041063492, 3.6927782082126983],
            "lots": [5, 1, 3],
            "total_lot": 9,
            "avg_price": 59477.77777777778,
            "risk_shares": [0.17578125, 0.1328125, 0.69140625],
            "total_risk": 10240,
            "loss_rate": 0.059534883720930236,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 53530,
            "margin_use": 0.31122093023255815,
            "losscut_price": 46314.444444444445,
            "losscut_distance": 13163.333333333334,
            "stop_distance": 1137.777777777781,
            "lc_before_stop": False,
            "immediate_lc": False,
            "cap_target": 58340,
            "cap_lot": 24.274737337305922,
            "scale": 1,
            "buildable_lot": 9,
            "effective_risk": 10240,
            "margin_binds": False,
        },
    ),
    "stop_invalid_long": dict(
        params=dict(direction='long', entry_prices=[58700, 59700],
                    stop_price=59000, take_price=None,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.07686131386861314, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [-300, 700],
            "entry_prices": [58700, 59700],
            "weights": [1, 2],
            "weighted_distance_sum": 1100,
            "base_lot": 12.018314532183144,
            "lots_raw": [12.018314532183144, 24.036629064366288],
            "lots": [12, 24],
            "total_lot": 36,
            "avg_price": 59366.666666666664,
            "risk_shares": [-0.2727272727272727, 1.2727272727272727],
            "total_risk": 13200,
            "loss_rate": 0.07674418604651163,
            "stop_price": 59000,
            "stop_invalid": True,
            "round_zeroed": False,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 213720,
            "margin_use": 1.2425581395348837,
            "losscut_price": 60525.555555555555,
            "losscut_distance": -1158.888888888889,
            "stop_distance": 366.66666666666424,
            "lc_before_stop": True,
            "immediate_lc": True,
            "cap_target": 59000,
            "cap_lot": 27.287149656266514,
            "scale": 0.7579763793407365,
            "buildable_lot": 27,
            "effective_risk": 9900,
            "margin_binds": True,
        },
    ),
    "round_zeroed": dict(
        params=dict(direction='long', entry_prices=[58700, 59700, 60700],
                    stop_price=58340, take_price=None,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.0005, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360, 1360, 2360],
            "entry_prices": [58700, 59700, 60700],
            "weights": [1, 2, 3],
            "weighted_distance_sum": 10160,
            "base_lot": 0.008464566929133858,
            "lots_raw": [0.008464566929133858, 0.016929133858267716, 0.025393700787401573],
            "lots": [0, 0, 0],
            "total_lot": 0,
            "avg_price": 0,
            "risk_shares": [0, 0, 0],
            "total_risk": 0,
            "loss_rate": 0,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": True,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 0,
            "margin_use": 0,
            "losscut_price": 0,
            "losscut_distance": 0,
            "stop_distance": 58340,
            "lc_before_stop": True,
            "immediate_lc": False,
            "cap_target": 58340,
            "cap_lot": math.inf,
            "scale": 1,
            "buildable_lot": 0,
            "effective_risk": 0,
            "margin_binds": False,
        },
    ),
    "immediate_lc": dict(
        params=dict(direction='long', entry_prices=[58700, 59700, 60700],
                    stop_price=58340, take_price=None,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.9, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='margin', win_rate=0.38),
        expected={
            "distances": [360, 1360, 2360],
            "entry_prices": [58700, 59700, 60700],
            "weights": [1, 2, 3],
            "weighted_distance_sum": 10160,
            "base_lot": 15.236220472440944,
            "lots_raw": [15.236220472440944, 30.47244094488189, 45.70866141732283],
            "lots": [15, 30, 45],
            "total_lot": 90,
            "avg_price": 60033.333333333336,
            "risk_shares": [0.03543307086614173, 0.2677165354330709, 0.6968503937007874],
            "total_risk": 152400,
            "loss_rate": 0.8860465116279069,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 540300,
            "margin_use": 3.141279069767442,
            "losscut_price": 64125.555555555555,
            "losscut_distance": -4092.222222222222,
            "stop_distance": 1693.3333333333358,
            "lc_before_stop": True,
            "immediate_lc": True,
            "cap_target": None,
            "cap_lot": 28.650749583564686,
            "scale": 0.3183416620396076,
            "buildable_lot": 28,
            "effective_risk": 47413.333333333336,
            "margin_binds": True,
        },
    ),
    "margin_binds_lc": dict(
        params=dict(direction='long', entry_prices=[58700, 59700, 60700],
                    stop_price=58340, take_price=None,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.4, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360, 1360, 2360],
            "entry_prices": [58700, 59700, 60700],
            "weights": [1, 2, 3],
            "weighted_distance_sum": 10160,
            "base_lot": 6.771653543307087,
            "lots_raw": [6.771653543307087, 13.543307086614174, 20.31496062992126],
            "lots": [6, 13, 20],
            "total_lot": 39,
            "avg_price": 60058.97435897436,
            "risk_shares": [0.032219570405727926, 0.2637231503579952, 0.7040572792362768],
            "total_risk": 67040,
            "loss_rate": 0.38976744186046514,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 234230,
            "margin_use": 1.3618023255813954,
            "losscut_price": 61654.61538461538,
            "losscut_distance": -1595.6410256410256,
            "stop_distance": 1718.9743589743593,
            "lc_before_stop": True,
            "immediate_lc": True,
            "cap_target": 58340,
            "cap_lot": 22.26574169349752,
            "scale": 0.5709164536794236,
            "buildable_lot": 22,
            "effective_risk": 37817.4358974359,
            "margin_binds": True,
        },
    ),
    "lc_cap_unbounded": dict(
        params=dict(direction='long', entry_prices=[58700],
                    stop_price=40000, take_price=None,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.07686131386861314, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [18700],
            "entry_prices": [58700],
            "weights": [1],
            "weighted_distance_sum": 18700,
            "base_lot": 0.7069596783637144,
            "lots_raw": [0.7069596783637144],
            "lots": [0],
            "total_lot": 0,
            "avg_price": 0,
            "risk_shares": [0],
            "total_risk": 0,
            "loss_rate": 0,
            "stop_price": 40000,
            "stop_invalid": False,
            "round_zeroed": True,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 0,
            "margin_use": 0,
            "losscut_price": 0,
            "losscut_distance": 0,
            "stop_distance": 40000,
            "lc_before_stop": True,
            "immediate_lc": False,
            "cap_target": 40000,
            "cap_lot": math.inf,
            "scale": 1,
            "buildable_lot": 0,
            "effective_risk": 0,
            "margin_binds": False,
        },
    ),
    "zero_fraction": dict(
        params=dict(direction='long', entry_prices=[58700, 59700],
                    stop_price=58340, take_price=None,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.0, balance=172000, point_value=1,
                    margin_rate=0.1, lot_mode='int',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360, 1360],
            "entry_prices": [58700, 59700],
            "weights": [1, 2],
            "weighted_distance_sum": 3080,
            "base_lot": 0,
            "lots_raw": [0, 0],
            "lots": [0, 0],
            "total_lot": 0,
            "avg_price": 0,
            "risk_shares": [0, 0],
            "total_risk": 0,
            "loss_rate": 0,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 0,
            "margin_use": 0,
            "losscut_price": 0,
            "losscut_distance": 0,
            "stop_distance": 58340,
            "lc_before_stop": True,
            "immediate_lc": False,
            "cap_target": 58340,
            "cap_lot": math.inf,
            "scale": 1,
            "buildable_lot": 0,
            "effective_risk": 0,
            "margin_binds": False,
        },
    ),
    "point_value_10": dict(
        params=dict(direction='long', entry_prices=[58700, 59700],
                    stop_price=58340, take_price=None,
                    weight_pattern='linear', custom_weights=None,
                    fraction=0.07686131386861314, balance=1720000, point_value=10,
                    margin_rate=0.1, lot_mode='dec',
                    cap_basis='lc', win_rate=0.38),
        expected={
            "distances": [360, 1360],
            "entry_prices": [58700, 59700],
            "weights": [1, 2],
            "weighted_distance_sum": 3080,
            "base_lot": 4.292255190065409,
            "lots_raw": [4.292255190065409, 8.584510380130817],
            "lots": [4.292255190065409, 8.584510380130817],
            "total_lot": 12.876765570196227,
            "avg_price": 59366.66666666666,
            "risk_shares": [0.11688311688311691, 0.8831168831168832],
            "total_risk": 132201.45985401457,
            "loss_rate": 0.07686131386861313,
            "stop_price": 58340,
            "stop_invalid": False,
            "round_zeroed": False,
            "rr": None,
            "profit_yen": None,
            "profit_rate": None,
            "breakeven": None,
            "excess": None,
            "ev_yen": None,
            "win_rate": 0.38,
            "required_margin": 764450.6493506492,
            "margin_use": 0.44444805194805187,
            "losscut_price": 51945.94175371952,
            "losscut_distance": 7420.7249129471365,
            "stop_distance": 1026.666666666657,
            "lc_before_stop": False,
            "immediate_lc": False,
            "cap_target": 58340,
            "cap_lot": 24.700813786500735,
            "scale": 1,
            "buildable_lot": 12.876765570196227,
            "effective_risk": 132201.45985401457,
            "margin_binds": False,
        },
    ),
}

# ---- サイクル 1: 分割ロット変換の中核（HTML :974-985 / :880-888） ----

_CORE_KEYS = (
    "distances", "entry_prices", "weights", "weighted_distance_sum", "base_lot",
    "lots_raw", "lots", "total_lot", "avg_price", "risk_shares", "total_risk",
    "loss_rate", "stop_price", "stop_invalid", "round_zeroed",
)


@pytest.mark.parametrize("case_id", sorted(GOLDEN))
def test_lot_conversion_matches_reference_implementation(case_id):
    """分割ロット変換の中核 15 項目が参照実装（direct 状態）の実測出力と厳密一致する。"""
    # Arrange
    case = GOLDEN[case_id]
    # Act
    plan = _plan(case)
    # Assert
    _check(plan, case, _CORE_KEYS)


# ---- サイクル 2: 利確ブロック（HTML :986-995） ----

_TAKE_KEYS = ("rr", "profit_yen", "profit_rate", "breakeven", "excess", "ev_yen", "win_rate")


@pytest.mark.parametrize("case_id", sorted(GOLDEN))
def test_take_profit_block_matches_reference_implementation(case_id):
    """RR・利益・分岐点・超過勝率・期待値が参照実装（direct 状態）と厳密一致する。

    利確未指定（take_price=None）のケースでは参照実装同様に全項目 None であること
    （:988 の ``TP>0 && totalLot>0`` ガード）も同じ表で固定する。
    """
    # Arrange
    case = GOLDEN[case_id]
    # Act
    plan = _plan(case)
    # Assert
    _check(plan, case, _TAKE_KEYS)


# ---- サイクル 3: 証拠金・ロスカット（HTML :998-1013・権威式を呼ぶ） ----

_MARGIN_KEYS = ("required_margin", "margin_use", "losscut_price", "losscut_distance",
                "stop_distance", "lc_before_stop", "immediate_lc")


@pytest.mark.parametrize("case_id", sorted(GOLDEN))
def test_margin_and_losscut_match_reference_implementation(case_id):
    """必要証拠金・使用率・ロスカット価格/距離・損切り距離・2 つの分岐が参照実装と一致する。"""
    # Arrange
    case = GOLDEN[case_id]
    # Act
    plan = _plan(case)
    # Assert
    _check(plan, case, _MARGIN_KEYS)


def test_margin_and_losscut_delegate_to_account_engine_authority(monkeypatch):
    """証拠金・ロスカットは `account_engine.official_*` を**呼ぶ**（式を書き下していない）。

    権威関数を差し替えると出力が追随することで、複製ではなく委譲であることを固定する
    （`sizing_ports.py:42-52` の複製禁止規律の機械的検査）。
    """
    # Arrange
    import simulator.usecase.split_entry_plan as sut
    case = GOLDEN["default_K3"]
    monkeypatch.setattr(sut, "official_required_margin", lambda *a, **k: 12345.0)
    monkeypatch.setattr(sut, "official_losscut_price", lambda *a, **k: 111.0)
    # Act
    plan = _plan(case)
    # Assert
    assert plan.required_margin == 12345.0
    assert plan.losscut_price == 111.0


# ---- サイクル 4: 建て制約（HTML :1015-1029） ----

_CAP_KEYS = ("cap_target", "cap_lot", "scale", "buildable_lot", "effective_risk", "margin_binds")


@pytest.mark.parametrize("case_id", sorted(GOLDEN))
def test_buildable_constraint_matches_reference_implementation(case_id):
    """建て制約（証拠金100% / ロスカット価格基準）と実建可能ロットが参照実装と一致する。"""
    # Arrange
    case = GOLDEN[case_id]
    # Act
    plan = _plan(case)
    # Assert
    _check(plan, case, _CAP_KEYS)


def test_lc_cap_delegates_to_account_engine_authority(monkeypatch):
    """cap_lot（lc 基準）は閉形式を書き下さず `official_losscut_price` に判定させる。

    権威関数を「常に安全」に差し替えると上限が消える（＝制限なし）ことで、上限判定が
    権威関数の戻り値のみに依存していることを固定する。
    """
    # Arrange
    import simulator.usecase.split_entry_plan as sut
    case = GOLDEN["default_K1"]           # 既定は cap_basis='lc' かつ制約が効いている
    assert case["expected"]["cap_lot"] < case["expected"]["total_lot"]
    monkeypatch.setattr(sut, "official_losscut_price", lambda *a, **k: -1e9)
    # Act
    plan = _plan(case)
    # Assert
    assert plan.cap_lot == math.inf
    assert plan.margin_binds is False


# ---- サイクル 5: 入力検証（無音の誤動作を作らない。edge_ruin.EdgeRuinSpec:141-155 と同型） ----

@pytest.mark.parametrize("override, needle", [
    (dict(direction="up"), "direction"),
    (dict(entry_prices=[]), "entry_prices"),
    (dict(entry_prices=[1.0] * 11), "entry_prices"),
    (dict(lot_mode="round"), "lot_mode"),
    (dict(cap_basis="equity"), "cap_basis"),
    (dict(weight_pattern="fib"), "weight_pattern"),
    (dict(point_value=0), "point_value"),
    (dict(margin_rate=-0.01), "margin_rate"),
    (dict(win_rate=1.5), "win_rate"),
])
def test_spec_rejects_out_of_range_input(override, needle):
    """範囲外入力は ValueError（黙って既定値へ倒さない）。"""
    # Arrange
    params = dict(GOLDEN["default_K3"]["params"])
    params.update(override)
    # Act / Assert
    with pytest.raises(ValueError, match=needle):
        SplitEntrySpec(**params)


def test_custom_weight_pattern_requires_enough_weights():
    """weight_pattern='custom' は custom_weights の欠落・長さ不足を明示失敗させる。"""
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match="custom_weights"):
        generate_weights(3, "custom", None)
    with pytest.raises(ValueError, match="custom_weights"):
        generate_weights(3, "custom", (1.0, 2.0))
