"""§3.1 / §3.2 の役割判定（水準 / 非水準・オシレータ宣言）を固定する。

§3.1: 水準でない系列の除外は**名前ではなく実値の桁**（現在値の 0.3〜3 倍）で判定する。
実測では `btlm_trail_beta`（−2〜218）/ `btlm_trail_sigma`（40〜5,591）/
`btlm_trail_band_hit_rate`（0.70〜0.92）を水準として数えていた誤りがあった（§11）。

§8 OCP: 「積み上がる量か」も性質の宣言で切り替え、指標名で分岐しない。
"""
from __future__ import annotations

import math

import pytest

from dashboard_ui.adapter.series_role_table import SeriesRoleTable
from dashboard_ui.usecase.sheet_models import SeriesRole, SheetInstance

PRICE = 65_760.0


def instance_of(indicator_id: str, params: dict, timeframe: str = "1m") -> SheetInstance:
    return SheetInstance.of(indicator_id, "default", params, chart_timeframe=timeframe)


def role(values, *, indicator_id: str = "btlm_trail", series_name: str = "x") -> SeriesRole:
    return SeriesRoleTable().role_of(
        instance=instance_of(indicator_id, {}),
        series_name=series_name,
        values=tuple(values),
        reference_price=PRICE,
    )


def test_a_series_on_the_price_scale_is_a_level() -> None:
    assert role([PRICE - 30.0, PRICE, PRICE + 30.0]) is SeriesRole.PRICE_LEVEL


@pytest.mark.parametrize(
    "values",
    [
        [-2.0, 100.0, 218.0],          # btlm_trail_beta（実測レンジ）
        [40.0, 900.0, 5_591.0],        # btlm_trail_sigma（実測レンジ）
        [0.70, 0.81, 0.92],            # btlm_trail_band_hit_rate（実測レンジ）
        [-0.22, -0.14, -0.06],         # ma_marod（乖離率 %）
        [31.9, 52.2, 67.4],            # rsi（0-100）
    ],
)
def test_a_series_off_the_price_scale_is_not_a_level(values) -> None:
    assert role(values) is SeriesRole.NOT_LEVEL


@pytest.mark.parametrize(
    "median, expected",
    [
        (0.3 * PRICE, SeriesRole.PRICE_LEVEL),          # 下限は**含む**
        (3.0 * PRICE, SeriesRole.PRICE_LEVEL),          # 上限は**含む**
        (0.3 * PRICE - 1.0, SeriesRole.NOT_LEVEL),
        (3.0 * PRICE + 1.0, SeriesRole.NOT_LEVEL),
    ],
)
def test_the_band_boundaries_are_inclusive(median: float, expected: SeriesRole) -> None:
    assert role([median, median, median]) is expected


def test_a_series_without_any_finite_value_is_not_a_level() -> None:
    """warm-up だけの系列は水準として数えない（並びを壊さない）。"""
    assert role([math.nan, math.nan]) is SeriesRole.NOT_LEVEL


def test_an_empty_series_is_not_a_level() -> None:
    assert role([]) is SeriesRole.NOT_LEVEL


# ------------------------------------------------------------ オシレータ宣言
def test_the_band_series_name_follows_the_configured_quantile() -> None:
    """帯の系列名は設定の `q_high` から作る（§7.1.1 の `_q{q_hi}` 展開と同じ規約）。"""
    table = SeriesRoleTable()

    marod = table.oscillator_spec(
        instance=instance_of("ma_marod", {"q_high": 0.95}),
        series_names=frozenset({"ma_marod", "ma_marod_q95"}),
    )
    rsi = table.oscillator_spec(
        instance=instance_of("profit_rsi", {"q_high": 0.90}),
        series_names=frozenset({"rsi", "rsi_q90"}),
    )

    assert (marod.value_series, marod.band_high_series) == ("ma_marod", "ma_marod_q95")
    assert (rsi.value_series, rsi.band_high_series) == ("rsi", "rsi_q90")


def test_the_quantile_defaults_come_from_the_indicator_catalog() -> None:
    """設定に無い水準パラメータは**カタログ既定**で埋まる（勝手な既定を発明しない）。"""
    table = SeriesRoleTable()

    spec = table.oscillator_spec(
        instance=instance_of("profit_rsi", {}),
        series_names=frozenset({"rsi", "rsi_q90"}),
    )

    assert spec.band_high_series == "rsi_q90"      # profit_rsi の既定 q_high = 0.90
    assert spec.q_high == 0.90
    assert (spec.window_n, spec.k_events) == (500, 50)


def test_the_settings_win_over_the_catalog_defaults() -> None:
    table = SeriesRoleTable()

    spec = table.oscillator_spec(
        instance=instance_of("profit_rsi", {"q_high": 0.80, "window_n": 200, "k_events": 9}),
        series_names=frozenset({"rsi", "rsi_q80"}),
    )

    assert (spec.band_high_series, spec.q_high) == ("rsi_q80", 0.80)
    assert (spec.window_n, spec.k_events) == (200, 9)


def test_only_tickvol_is_declared_cumulative() -> None:
    """§5.3.3: 積み上がる量かどうかは**性質の宣言**（指標名での分岐を呼び出し側に作らない）。"""
    table = SeriesRoleTable()

    cumulative = {
        indicator_id: table.oscillator_spec(
            instance=instance_of(indicator_id, {}),
            series_names=frozenset(),
        ).cumulative
        for indicator_id in ("ma_marod", "btlm_trail_marod", "profit_rsi", "tickvol")
    }

    assert cumulative == {"ma_marod": False, "btlm_trail_marod": False,
                          "profit_rsi": False, "tickvol": True}


def test_the_rsi_excess_is_normalised_by_the_headroom() -> None:
    """RSI は有界量なので超過分を余地 `100 - u` で割る（`profit_rsi/src/levels.py` ③）。"""
    table = SeriesRoleTable()

    rsi = table.oscillator_spec(
        instance=instance_of("profit_rsi", {}), series_names=frozenset()
    )
    tickvol = table.oscillator_spec(
        instance=instance_of("tickvol", {}), series_names=frozenset()
    )

    assert rsi.excess(95.0, 90.0) == 0.5
    assert tickvol.excess(95.0, 90.0) == 5.0


def test_a_price_scale_indicator_has_no_oscillator_cell() -> None:
    table = SeriesRoleTable()

    assert table.oscillator_spec(
        instance=instance_of("moving_averages", {}), series_names=frozenset({"MA"})
    ) is None


# ------------------------------------------------------------------ 行ラベル
def test_the_row_label_separates_instances_that_differ_in_settings() -> None:
    """§11-2: ラベルはパラメータまで含めて一意にする（同じ足で衝突させない）。"""
    table = SeriesRoleTable()

    first = table.row_label(
        instance=instance_of("moving_averages", {"ma_type": "ema", "length": 24}),
        series_name="MA",
    )
    second = table.row_label(
        instance=instance_of("moving_averages", {"ma_type": "ema", "length": 200}),
        series_name="MA",
    )

    assert first != second
    assert first.startswith("MA")


def test_the_row_label_names_each_parameter() -> None:
    """裸の値（`False`・`981`）が何のパラメータか読者に復元できるよう `名前=値` で添える
    （依頼者承認 2026-08-30。値だけを並べる旧形式への回帰を禁じる）。"""
    table = SeriesRoleTable()

    label = table.row_label(
        instance=instance_of("moving_averages", {"length": 24, "source": "low"}),
        series_name="MA",
    )

    assert "length=24" in label
    assert "source=low" in label


def test_the_row_naming_splits_name_period_and_source() -> None:
    """依頼者指示 2026-08-30: 指標名 / 期間 / ソースの 3 分割。既定どおりでも常に値を返す。"""
    table = SeriesRoleTable()

    naming = table.row_naming(
        instance=instance_of("moving_averages", {"length": 24, "source": "low"}),
        series_name="MA",
    )

    assert naming["name"] == "MA"
    assert naming["level"] == ""            # 接頭辞規約に乗らない系列は水準なし
    assert naming["period"] == 24
    assert naming["source"] == "low"
    assert "length" not in naming["extra"] and "source" not in naming["extra"]


def test_the_row_naming_splits_the_level_out_of_the_series_name() -> None:
    """依頼者指示 2026-08-30: q95 等の水準も列へ分割（系列名 = <indicator>_<水準> の規約）。"""
    table = SeriesRoleTable()

    naming = table.row_naming(
        instance=instance_of("btlm_trail", {"maxbars": 156}),
        series_name="btlm_trail_q95",
    )

    assert naming["name"] == "btlm_trail"
    assert naming["level"] == "q95"       # モックも原語のまま＝変換しない


def test_the_level_tokens_are_shown_in_japanese_with_effective_sigma() -> None:
    """依頼者指示 2026-08-30: u1→内側上 1σ の形。σ は実効値（既定の写しではない）。"""
    table = SeriesRoleTable()

    inner = table.row_naming(
        instance=instance_of("cvfe", {"sigma_inner": 2.0}), series_name="cvfe_u1"
    )
    outer_default = table.row_naming(
        instance=instance_of("cvfe", {}), series_name="cvfe_l2"
    )
    off = table.row_naming(
        instance=instance_of("btlm_trail", {}), series_name="btlm_trail_off_hi"
    )

    assert inner["level"] == "内側上 2σ"
    assert outer_default["level"] == "外側下 2σ"   # カタログ既定 sigma_outer=2.0
    assert off["level"] == "外れ上"


def test_the_level_p_is_the_defining_quantile_only_for_quantile_levels() -> None:
    """依頼者裁定 2026-08-30: 水準セルは定義分位 p で塗る。q{pct} 系のみ p を持ち、
    σ 帯・mean は p 目盛りに載らない（None＝無色。無言で 0.5 を埋めない）。"""
    table = SeriesRoleTable()

    def p_of(indicator, series):
        return table.row_naming(
            instance=instance_of(indicator, {}), series_name=series
        )["level_p"]

    assert p_of("btlm_trail", "btlm_trail_q95") == 0.95
    assert p_of("btlm_trail", "btlm_trail_q5") == 0.05


def test_every_declared_level_maps_to_its_stated_quantile() -> None:
    """依頼者承認 2026-08-30: 宣言された極端度で統一。外れ=実効 q_out・中心=0.5・σ=Φ(±k)。"""
    table = SeriesRoleTable()

    def naming_of(indicator, params, series):
        return table.row_naming(
            instance=instance_of(indicator, params), series_name=series
        )

    off_hi = naming_of("btlm_trail", {"q_out": 0.99}, "btlm_trail_off_hi")
    off_lo = naming_of("btlm_trail", {"q_out": 0.99}, "btlm_trail_off_lo")
    assert off_hi["level_p"] == 0.99 and off_hi["level_note"] is None
    assert off_lo["level_p"] == pytest.approx(0.01)

    assert naming_of("btlm_trail", {}, "btlm_trail_mean")["level_p"] == 0.5
    assert naming_of("cvfe", {}, "cvfe_mid")["level_p"] == 0.5

    u2 = naming_of("cvfe", {"sigma_outer": 2.0}, "cvfe_u2")
    l1 = naming_of("cvfe", {"sigma_inner": 1.0}, "cvfe_l1")
    assert u2["level_p"] == pytest.approx(0.97725, abs=1e-4)
    assert l1["level_p"] == pytest.approx(1 - 0.84134, abs=1e-4)
    assert "正規換算" in u2["level_note"]

    # 宣言の無い外れ（q_out 未設定・カタログ既定 null）は色を置かない
    assert naming_of("btlm_trail", {}, "btlm_trail_off_hi")["level_p"] is None


def test_the_row_naming_uses_catalog_defaults_when_params_are_omitted() -> None:
    table = SeriesRoleTable()

    naming = table.row_naming(
        instance=instance_of("moving_averages", {}), series_name="MA"
    )

    assert naming["period"] == 9          # カタログ既定 length
    assert naming["source"] == "close"    # カタログ既定 source


def test_the_row_naming_keeps_other_non_default_params_in_extra() -> None:
    table = SeriesRoleTable()

    naming = table.row_naming(
        instance=instance_of("cvfe", {"n_har": 981, "sigma_outer": 3.0}),
        series_name="cvfe_u2",
    )

    assert naming["period"] == 981
    assert naming["extra"] == "sigma_outer=3.0"


def test_the_row_naming_drops_params_that_do_not_change_the_level_values() -> None:
    """描画・凡例・付随メトリクスにしか効かない設定は伝える情報が無い（依頼者指摘 2026-08-30）。"""
    table = SeriesRoleTable()

    naming = table.row_naming(
        instance=instance_of(
            "btlm_trail",
            {"maxbars": 156, "show_metrics": False, "n_cov": 495, "q_out": 0.999},
        ),
        series_name="btlm_trail_q95",
    )

    assert "show_metrics" not in naming["extra"]
    assert "n_cov" not in naming["extra"]
    assert "q_out=0.999" in naming["extra"]   # 水準の定義に効くものは残る


def test_the_row_label_drops_parameters_unknown_to_the_catalog() -> None:
    """撤去済みパラメータの残骸（例: `wait_for_close`・ISSUE-286 で撤去）が保存済み
    テンプレートから漏れてもラベルへ出さない（計算に使われない値を表示しない。
    依頼者承認 2026-08-30）。"""
    table = SeriesRoleTable()

    label = table.row_label(
        instance=instance_of(
            "moving_averages", {"length": 24, "wait_for_close": False}
        ),
        series_name="MA",
    )

    assert label == "MA length=24"


def test_the_row_label_is_stable_for_the_same_settings() -> None:
    table = SeriesRoleTable()
    params = {"length": 24, "ma_type": "ema", "source": "hlc3"}

    first = table.row_label(instance=instance_of("moving_averages", params), series_name="MA")
    second = table.row_label(
        instance=instance_of("moving_averages", dict(reversed(list(params.items())))),
        series_name="MA",
    )

    assert first == second


def test_a_missing_level_parameter_is_an_explicit_error() -> None:
    """水準パラメータの既定を発明しない（黙って別の帯を読みに行かせない）。

    設定にもカタログにも無い場合、適当な既定で `rsi_q90` を読みに行くと、実際には別の帯で
    動いているセルが「正常」に見えてしまう。供給の欠落は明示的に落とす（§7 無言の縮退禁止）。
    """
    table = SeriesRoleTable(param_defaults={"profit_rsi": {"window_n": 500, "k_events": 50}})

    with pytest.raises(KeyError, match="q_high"):
        table.oscillator_spec(
            instance=instance_of("profit_rsi", {}), series_names=frozenset()
        )


# ---------------------------------------- 積み上がる量は価格水準になり得ない（ISSUE-462）
def test_a_cumulative_quantity_is_never_a_price_level_even_at_price_magnitude() -> None:
    """実 UI で発生した欠陥の再現: 週足のティック数（中央値 ~10 万）が「現在値の 0.3〜3 倍」の
    帯へ偶然入り、tickvol 260 が価格 488,103 円の行としてラダーに出た。
    裁定（2026-08-29・設計書改訂履歴）: tickvol はラダーに一切出さない。
    除外は名前の列挙ではなく**性質**（cumulative 宣言＝件数は価格ではない）で行う。
    """
    price_magnitude = [0.5 * PRICE, 1.5 * PRICE, 2.9 * PRICE]

    verdict = role(price_magnitude, indicator_id="tickvol", series_name="tickvol_q90")

    assert verdict is SeriesRole.NOT_LEVEL


def test_a_non_cumulative_series_at_price_magnitude_stays_a_level() -> None:
    """性質による除外が過剰でないこと（cvfe / MA 等の価格系列は従来どおり水準）。"""
    assert role([PRICE - 30.0, PRICE, PRICE + 30.0]) is SeriesRole.PRICE_LEVEL
