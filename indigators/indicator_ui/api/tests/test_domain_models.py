"""SeriesDef / IndicatorDef / AppliedInstance / Favorite 値オブジェクトの仕様検証。

対象: indigators/indicator_ui/api/domain/
  - series_def.py    : SeriesKind / LineStyle / SeriesDef
  - indicator_def.py : Tab / Placement / Group / CategoryRef / ComputeEntry / IndicatorDef
  - applied_instance.py : AppliedInstance（generation 不変ルール）
  - favorite.py      : Favorite
設計入力:
  - 内部設計書 §3.1.2（SeriesDef・source_column/series_name 分離 申し送り3）、
    §3.1.3（IndicatorDef/ComputeEntry/CategoryRef）、
    §3.1.4（AppliedInstance・accepts/next_generation 申し送り5）、
    §3.1.6 相当（Favorite）、§6.1 AppliedInstanceJSON、
    クラス図 §（SeriesDef/AppliedInstance/Favorite 属性）
  - 基本設計書 §5.2 主要エンティティ定義（SERIES_DEF / FAVORITE 属性）
実コード根拠（テストケースの正当性）:
  - profit_band/src/lwc_chart.py:136-137（col="pOL_99" vs name="pOL 99%" 不一致）
  - tgp_btlm/src/lwc_chart.py:111（値列名はライン名と一致させる規約）

import 規約: conftest.py が api/ を sys.path へ追加 → from domain import ...（テスト基盤集約）。
テスト構造: Arrange-Act-Assert（AAA）。各テストは独立・再現可能（F.I.R.S.T）。
"""

import pytest

# import パス（api/）は conftest.py で通す。
from domain.series_def import LineStyle, SeriesDef, SeriesKind
from domain.indicator_def import (
    CategoryRef,
    ComputeEntry,
    Group,
    IndicatorDef,
    Placement,
    Tab,
)
from domain.applied_instance import AppliedInstance
from domain.favorite import Favorite
from domain.param_def import Constraint, ConstraintKind, ParamDef, ParamType


# ===========================================================================
# SeriesDef（§3.1.2 — source_column / series_name 分離が核心）
# ===========================================================================


def test_series_kind_enum_has_line_and_horizontal_line():
    # Assert（§3.1.2：2 種別）
    kinds = {k.value for k in SeriesKind}
    assert kinds == {"line", "horizontal_line"}


def test_line_style_enum_has_solid_dotted_dashed():
    # Assert（§3.1.2：3 線種）
    styles = {s.value for s in LineStyle}
    assert styles == {"solid", "dotted", "dashed"}


def test_series_def_holds_kind_source_column_and_series_name_separately():
    # Arrange / Act（profit_band: col と name が別物 lwc_chart.py:136-137）
    s = SeriesDef(
        kind=SeriesKind.LINE,
        source_column="pOL_99",
        series_name="pOL 99%",
        dynamic=True,
        style=LineStyle.SOLID,
        width=1,
    )
    # Assert（D-1 是正: 2 属性が別値を保持）
    assert s.kind is SeriesKind.LINE
    assert s.source_column == "pOL_99"
    assert s.series_name == "pOL 99%"
    assert s.source_column != s.series_name
    assert s.dynamic is True
    assert s.style is LineStyle.SOLID
    assert s.width == 1


def test_series_def_optional_fields_default_to_none_or_false():
    # Arrange / Act（最小構成: pattern/style/color 等は任意）
    s = SeriesDef(
        kind=SeriesKind.HORIZONTAL_LINE,
        source_column=None,
        series_name="bull#0",
        dynamic=True,
    )
    # Assert（規定値）
    assert s.source_column_pattern is None
    assert s.series_name_pattern is None
    assert s.style is None
    assert s.width is None
    assert s.color_rule is None
    assert s.price_scale_id is None
    assert s.axis_label_visible is False


def test_series_def_is_frozen_immutable():
    # Arrange
    s = SeriesDef(
        kind=SeriesKind.LINE,
        source_column="btlm_mean",
        series_name="btlm_mean",
        dynamic=False,
    )
    # Act / Assert（frozen dataclass）
    with pytest.raises(Exception):
        s.series_name = "x"  # type: ignore[misc]


# ===========================================================================
# IndicatorDef / ComputeEntry / CategoryRef（§3.1.3）
# ===========================================================================


def test_tab_enum_has_four_tabs():
    # Assert（§3.1.3：indicator/strategy/profile/pattern）
    assert {t.value for t in Tab} == {"indicator", "strategy", "profile", "pattern"}


def test_placement_enum_has_overlay_and_pane():
    # Assert
    assert {p.value for p in Placement} == {"overlay", "pane"}


def test_group_enum_has_personal_builtin_community():
    # Assert
    assert {g.value for g in Group} == {"personal", "builtin", "community"}


def test_category_ref_holds_group_and_name_key():
    # Arrange / Act
    c = CategoryRef(group=Group.BUILTIN, name_key="cat.technical")
    # Assert
    assert c.group is Group.BUILTIN
    assert c.name_key == "cat.technical"


def test_compute_entry_holds_compute_id_and_required_columns():
    # Arrange / Act（全 3 指標: OHLC 必須）
    e = ComputeEntry(
        compute_id="tgp_btlm",
        required_columns=("open", "high", "low", "close"),
        time_required=True,
        backend_param="fitter",
    )
    # Assert
    assert e.compute_id == "tgp_btlm"
    assert e.required_columns == ("open", "high", "low", "close")
    assert e.time_required is True
    assert e.backend_param == "fitter"


def test_compute_entry_variants_default_to_default_tuple():
    # Arrange / Act（既定 variant）
    e = ComputeEntry(
        compute_id="price_range_power",
        required_columns=("open", "high", "low", "close"),
        time_required=False,
    )
    # Assert（規定値）
    assert e.variants == ("default",)
    assert e.backend_param is None


def test_compute_entry_supports_multiple_variants():
    # Arrange / Act（profit_band: global / robust）
    e = ComputeEntry(
        compute_id="profit_band",
        required_columns=("open", "high", "low", "close"),
        time_required=True,
        variants=("global", "robust"),
    )
    # Assert
    assert e.variants == ("global", "robust")


def test_indicator_def_holds_id_params_series_and_compute():
    # Arrange
    pdef = ParamDef(name="q_low", label_key="label.q_low", type=ParamType.FLOAT, default=0.05)
    sdef = SeriesDef(
        kind=SeriesKind.LINE,
        source_column="btlm_mean",
        series_name="btlm_mean",
        dynamic=False,
    )
    compute = ComputeEntry(
        compute_id="tgp_btlm",
        required_columns=("open", "high", "low", "close"),
        time_required=True,
    )
    # Act
    d = IndicatorDef(
        id="tgp_btlm",
        display_name_key="ind.tgp_btlm",
        category=CategoryRef(group=Group.BUILTIN, name_key="cat.technical"),
        tab=Tab.INDICATOR,
        placement=Placement.OVERLAY,
        params=(pdef,),
        series=(sdef,),
        compute=compute,
    )
    # Assert
    assert d.id == "tgp_btlm"
    assert d.tab is Tab.INDICATOR
    assert d.placement is Placement.OVERLAY
    assert d.params == (pdef,)
    assert d.series == (sdef,)
    assert d.compute is compute
    assert d.description_key is None


def test_indicator_def_is_frozen_immutable():
    # Arrange
    d = IndicatorDef(
        id="x",
        display_name_key="k",
        category=CategoryRef(group=Group.PERSONAL, name_key="c"),
        tab=Tab.INDICATOR,
        placement=Placement.PANE,
        params=(),
        series=(
            SeriesDef(kind=SeriesKind.LINE, source_column="m", series_name="m", dynamic=False),
        ),
        compute=ComputeEntry(compute_id="x", required_columns=(), time_required=False),
    )
    # Act / Assert
    with pytest.raises(Exception):
        d.id = "y"  # type: ignore[misc]


# ===========================================================================
# AppliedInstance（§3.1.4 — generation 不変ルール 申し送り5）
# ===========================================================================


def _instance(generation=0, seq=1):
    return AppliedInstance(
        instance_id=f"profit_band#{seq}",
        indicator_id="profit_band",
        variant="global",
        params=(("probabilities", (0.95, 0.99)),),
        visible=True,
        generation=generation,
        seq=seq,
        created_at="2026-06-07T00:00:00Z",
    )


def test_applied_instance_holds_identity_and_generation_state():
    # Arrange / Act
    inst = _instance(generation=2, seq=3)
    # Assert
    assert inst.instance_id == "profit_band#3"
    assert inst.indicator_id == "profit_band"
    assert inst.variant == "global"
    assert inst.params == (("probabilities", (0.95, 0.99)),)
    assert inst.visible is True
    assert inst.generation == 2
    assert inst.seq == 3
    assert inst.created_at == "2026-06-07T00:00:00Z"


def test_applied_instance_next_generation_increments_by_one():
    # Arrange
    inst = _instance(generation=0)
    # Act
    nxt = inst.next_generation()
    # Assert（単調増加 +1）
    assert nxt.generation == 1


def test_applied_instance_next_generation_returns_new_instance_without_mutating():
    # Arrange（frozen: 元は不変）
    inst = _instance(generation=5)
    # Act
    nxt = inst.next_generation()
    # Assert（元の generation は据え置き＝新オブジェクト返却）
    assert inst.generation == 5
    assert nxt is not inst


def test_applied_instance_next_generation_preserves_other_fields():
    # Arrange
    inst = _instance(generation=1, seq=7)
    # Act
    nxt = inst.next_generation()
    # Assert（generation 以外は不変）
    assert nxt.instance_id == inst.instance_id
    assert nxt.indicator_id == inst.indicator_id
    assert nxt.variant == inst.variant
    assert nxt.params == inst.params
    assert nxt.visible == inst.visible
    assert nxt.seq == inst.seq
    assert nxt.created_at == inst.created_at


def test_applied_instance_accepts_true_when_response_generation_matches():
    # Arrange（現行 gen=3 の応答のみ採用 §6.6 レース対策）
    inst = _instance(generation=3)
    # Act / Assert
    assert inst.accepts(3) is True


def test_applied_instance_accepts_false_when_response_generation_is_stale():
    # Arrange（古い応答 gen=2 を破棄）
    inst = _instance(generation=3)
    # Act / Assert
    assert inst.accepts(2) is False


def test_applied_instance_accepts_false_when_response_generation_is_newer():
    # Arrange（一致しない未来世代も不採用）
    inst = _instance(generation=3)
    # Act / Assert
    assert inst.accepts(4) is False


def test_applied_instance_is_frozen_immutable():
    # Arrange
    inst = _instance()
    # Act / Assert
    with pytest.raises(Exception):
        inst.generation = 99  # type: ignore[misc]


# ===========================================================================
# Favorite（§5.2 FAVORITE — ★ 登録は指標 id 単位）
# ===========================================================================


def test_favorite_holds_indicator_id():
    # Arrange / Act
    f = Favorite(indicator_id="tgp_btlm")
    # Assert
    assert f.indicator_id == "tgp_btlm"


def test_favorite_is_frozen_immutable():
    # Arrange
    f = Favorite(indicator_id="tgp_btlm")
    # Act / Assert
    with pytest.raises(Exception):
        f.indicator_id = "x"  # type: ignore[misc]


def test_favorite_equality_by_indicator_id():
    # Arrange / Act（frozen dataclass は値等価）
    a = Favorite(indicator_id="profit_band")
    b = Favorite(indicator_id="profit_band")
    # Assert（集合での add/remove 重複判定の基礎）
    assert a == b
    assert len({a, b}) == 1


# ===========================================================================
# SeriesDef.resolve_series_name（§3.1.2 F3 照合基準＝series_name・source_column 非使用）
# ===========================================================================


def test_resolve_series_name_returns_series_name_for_matching_source_column():
    # Arrange（profit_band: source_column="pOL_99" ↔ series_name="pOL 99%"）
    s = SeriesDef(
        kind=SeriesKind.LINE,
        source_column="pOL_99",
        series_name="pOL 99%",
        dynamic=False,
    )
    # Act（値列名を渡すと描画系列名へ解決＝F3 基準は series_name）
    result = s.resolve_series_name("pOL_99")
    # Assert（source_column ではなく series_name を返す）
    assert result == "pOL 99%"


def test_resolve_series_name_ignores_column_and_yields_series_name_when_unequal():
    # Arrange（不一致系列: 列名と異なる値を渡しても series_name 基準で解決）
    s = SeriesDef(
        kind=SeriesKind.LINE,
        source_column="pOL_99",
        series_name="pOL 99%",
        dynamic=False,
    )
    # Act（source_column では照合しないことの固定: 別の列名でも series_name）
    result = s.resolve_series_name("other_col")
    # Assert（照合基準が source_column でない＝常に series_name を返す）
    assert result == "pOL 99%"
    assert result != s.source_column


def test_resolve_series_name_for_matching_pair_tgp_btlm():
    # Arrange（tgp_btlm: source_column == series_name の一致系列）
    s = SeriesDef(
        kind=SeriesKind.LINE,
        source_column="btlm_mean",
        series_name="btlm_mean",
        dynamic=False,
    )
    # Act
    result = s.resolve_series_name("btlm_mean")
    # Assert（一致系列でも series_name を返す＝両者一致を許容）
    assert result == "btlm_mean"


# ===========================================================================
# IndicatorDef.validate_params（§3.1.5 委譲）/ series>=1 不変条件（§3.1.3）
# ===========================================================================


def _indicator_with_q_constraints():
    """q_low<q_high 制約付き IndicatorDef（ConstraintEvaluator 委譲検証用）。"""
    return IndicatorDef(
        id="tgp_btlm",
        display_name_key="ind.tgp_btlm",
        category=CategoryRef(group=Group.BUILTIN, name_key="cat.technical"),
        tab=Tab.INDICATOR,
        placement=Placement.OVERLAY,
        params=(
            ParamDef(
                name="q_low",
                label_key="label.q_low",
                type=ParamType.FLOAT,
                default=0.05,
                constraints=(
                    Constraint(ConstraintKind.LT, ("q_low", "q_high"), "err.q_order"),
                ),
            ),
            ParamDef(
                name="q_high",
                label_key="label.q_high",
                type=ParamType.FLOAT,
                default=0.95,
            ),
        ),
        series=(
            SeriesDef(
                kind=SeriesKind.LINE,
                source_column="btlm_mean",
                series_name="btlm_mean",
                dynamic=False,
            ),
        ),
        compute=ComputeEntry(
            compute_id="tgp_btlm",
            required_columns=("open", "high", "low", "close"),
            time_required=True,
        ),
    )


def test_validate_params_returns_empty_when_values_valid():
    # Arrange（正常系: 0.05 < 0.95）
    d = _indicator_with_q_constraints()
    # Act（ConstraintEvaluator.evaluate へ委譲）
    result = d.validate_params({"q_low": 0.05, "q_high": 0.95})
    # Assert
    assert result == []


def test_validate_params_delegates_constraint_violation_to_evaluator():
    # Arrange（異常系: 逆転 0.96 > 0.5 → lt(q_low,q_high) 違反）
    d = _indicator_with_q_constraints()
    # Act
    result = d.validate_params({"q_low": 0.96, "q_high": 0.5})
    # Assert（評価器の違反がそのまま伝播）
    assert len(result) == 1
    assert result[0].param == "q_low"
    assert result[0].constraint == "lt(q_low,q_high)"


def test_validate_params_propagates_required_missing_violation():
    # Arrange（必須欠落: default=None の param を未指定）
    d = IndicatorDef(
        id="x",
        display_name_key="k",
        category=CategoryRef(group=Group.BUILTIN, name_key="c"),
        tab=Tab.INDICATOR,
        placement=Placement.OVERLAY,
        params=(
            ParamDef(name="q_low", label_key="label.q_low", type=ParamType.FLOAT, default=None),
        ),
        series=(
            SeriesDef(kind=SeriesKind.LINE, source_column="m", series_name="m", dynamic=False),
        ),
        compute=ComputeEntry(compute_id="x", required_columns=(), time_required=False),
    )
    # Act（q_low 未指定 → required 違反が評価器から伝播）
    result = d.validate_params({})
    # Assert
    assert len(result) == 1
    assert result[0].param == "q_low"
    assert result[0].constraint == "required"


def test_indicator_def_rejects_empty_series_invariant():
    # Arrange / Act / Assert（series>=1 不変条件: series 空は構築不可）
    with pytest.raises(ValueError):
        IndicatorDef(
            id="x",
            display_name_key="k",
            category=CategoryRef(group=Group.BUILTIN, name_key="c"),
            tab=Tab.INDICATOR,
            placement=Placement.OVERLAY,
            params=(),
            series=(),  # 空 series は違反
            compute=ComputeEntry(compute_id="x", required_columns=(), time_required=False),
        )


# ===========================================================================
# IndicatorDef.matches（§4.6 検索: 表示名+id・小文字・部分一致・複数語論理積）
# ===========================================================================


def _searchable_indicator(id_="tgp_btlm"):
    return IndicatorDef(
        id=id_,
        display_name_key="ind.tgp_btlm",
        category=CategoryRef(group=Group.BUILTIN, name_key="cat.technical"),
        tab=Tab.INDICATOR,
        placement=Placement.OVERLAY,
        params=(),
        series=(
            SeriesDef(kind=SeriesKind.LINE, source_column="m", series_name="m", dynamic=False),
        ),
        compute=ComputeEntry(compute_id=id_, required_columns=(), time_required=False),
    )


def test_matches_by_display_name_partial_case_insensitive():
    # Arrange（表示名 "TGP BTLM Channel" 部分一致・大小無視）
    d = _searchable_indicator()
    # Act
    result = d.matches("btlm", display_name="TGP BTLM Channel")
    # Assert（小文字化部分一致）
    assert result is True


def test_matches_by_id_partial_case_insensitive():
    # Arrange（id 一致: 表示名に無くても id で引ける §4.6）
    d = _searchable_indicator(id_="tgp_btlm")
    # Act（大文字入力でも id 部分一致）
    result = d.matches("TGP", display_name="Regression Channel")
    # Assert
    assert result is True


def test_matches_returns_false_when_query_absent_in_both():
    # Arrange（表示名にも id にも無い語）
    d = _searchable_indicator()
    # Act
    result = d.matches("nonexistent", display_name="TGP BTLM Channel")
    # Assert
    assert result is False


def test_matches_multiple_words_require_all_terms_conjunctively():
    # Arrange（複数語論理積: 両語が表示名+id 連結に含まれる）
    d = _searchable_indicator(id_="tgp_btlm")
    # Act（"btlm" と "channel" の論理積。前者は id、後者は表示名）
    result = d.matches("btlm channel", display_name="TGP BTLM Channel")
    # Assert（両語ヒット → 真）
    assert result is True


def test_matches_multiple_words_false_when_one_term_missing():
    # Arrange（論理積: 1 語が欠けると不一致）
    d = _searchable_indicator(id_="tgp_btlm")
    # Act（"btlm" はヒットするが "absent" は無い）
    result = d.matches("btlm absent", display_name="TGP BTLM Channel")
    # Assert（論理積のため偽）
    assert result is False


def test_matches_empty_query_returns_true():
    # Arrange（空クエリ＝フィルタ無し: 全件通過 §4.6 インクリメンタル初期状態）
    d = _searchable_indicator()
    # Act
    result = d.matches("", display_name="TGP BTLM Channel")
    # Assert（語が無い＝論理積は空真）
    assert result is True
