"""保証境界（非対象）N-01〜N-17 の宣言表（基本設計 §4.6・内部設計 §8.4.4）。

1. 層名/責務:
    main 層（Composition Root）。「本実装が保証しない設定」の宣言表を**組み立てる唯一の
    場所**であり、そのうち**設定の語彙を読む**宣言（N-02 / N-03 / N-07 / N-09 / N-11 /
    N-15 / N-16）を所有する。判定の所有者を閉じることで、非対象の追加が既存の分岐・関数の
    書き換えを要さない（OCP）。

    run 自身の引数だけで判定できる宣言（N-01 / N-05 / N-10 / N-17）と宣言の**型**は
    `simulator/main/unsupported_run_scope.py` が所有する（ISSUE-525）。分けた理由は
    そちらの docstring §2 にある——合流点（`simulator.main.build_interactor`）がそれらを
    適用する必要があり、親パッケージが子を import するとパッケージ間の双方向依存
    （ISSUE-502 の C-2）が復活するためである。**宣言はどちらでも 1 箇所**であり、
    置き場所は「その判定入力を所有するのは誰か」が決める。本モジュールはそれらを
    再輸出するので、呼出側の import は従来のままでよい。

2. 含む構造:
    UNSUPPORTED_RULES     : 宣言表（評価順。合流点側の宣言を織り込んだ全体）。
    RULES                 : ID → 宣言（唯一の索引）。
    RUN_REQUEST_RULES     : 実行要求時に評価する宣言（評価順）。
    SETTINGS_SCOPE_RULES  : 設定の語彙を読む宣言（写像層で適用する）。
    NON_RAISING_RULES     : 送出を伴わない非対象（欠番・近似・責務境界・ロード時）。
    apply_unsupported_rules : 設定の語彙を読む宣言の一括評価（最初の 1 件で Fail-Stop）。

3. 元 MQL 対応:
    MT5 Settings タブの各コントロールが表す機能のうち、本移植が再現しないもの
    （最適化・フォワード・visual・pips 建て損益・クロス通貨・実ティック等）。

4. 依存:
    標準: dataclasses / typing
    外部: なし
    プロジェクト内: simulator.main.unsupported_run_scope（宣言の型・合流点側の宣言・
                        適用器。本モジュールはそれらを再輸出する）/
                    simulator.usecase.tester_settings（DTO・列挙）/
                    simulator.adapter.tester_settings.ini_codec（生トークン表記の唯一の宣言。
                        UI 束縛のトークンを字形ごと書き直さないために公開フォーマッタを使う）

方針（基本設計 §4.6）: 非対象を沈黙スキップしない。非対象設定を実行要求された場合は
例外を送出して run を中止する（Fail-Stop）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from simulator.adapter.tester_settings.ini_codec import (
    format_bool_token,
    format_int_token,
)
# ISSUE-525: 宣言の型と、合流点で適用する宣言・適用器の所有者。**再輸出する**——
#   `RULES` / `UI_TRIGGER_*` / `UnsupportedRule` の呼出点は本番・検定に多数あり、
#   所在の変更を呼出側へ波及させない（値と規則の所有者は 1 箇所のままである）。
from simulator.main.unsupported_run_scope import (  # noqa: F401  (re-export: 公開 API)
    NOT_VIOLATED,
    RULE_REAL_TICKS_WITHOUT_STORE,
    RULE_MULTI_SYMBOL,
    RULE_SPREAD_DEPENDENT_EA_ON_SPREADLESS_DATA,
    RULE_UNKNOWN_EA,
    RUN_SCOPE_DECLARATIONS,
    RUN_SCOPE_INPUTS,
    RUN_SCOPE_RULES,
    UI_TRIGGER_EXCEPT_TOKENS,
    UI_TRIGGER_MODES,
    UI_TRIGGER_NONE,
    UI_TRIGGER_OFF_CANDIDATES,
    UI_TRIGGER_OFF_PROFILE,
    UI_TRIGGER_ON_PRESENCE,
    UI_TRIGGER_ON_TOKENS,
    UI_TRIGGERS_WITH_TOKENS,
    RunScopeInputs,
    UiTrigger,
    UnsupportedRule,
    apply_run_scope_unsupported_rules,
    raise_unsupported,
    select_run_scope_rules,
)
from simulator.usecase.tester_settings import (
    DatesPreset,
    EffectiveSettings,
    ForwardMode,
    OptimizationMode,
)

if TYPE_CHECKING:  # 型注釈専用（実行時は import しない＝循環回避）
    from simulator.main.tester_settings.kwargs_mapper import EngineBinding


# ---------------------------------------------------------------------------
# 判定式（実行要求時）
# ---------------------------------------------------------------------------


def _detect_optimization(effective: EffectiveSettings, _binding: "EngineBinding") -> Any:
    optimization = effective.optimization
    if optimization is None or optimization is OptimizationMode.DISABLED:
        return NOT_VIOLATED
    return int(optimization)


def _detect_forward(effective: EffectiveSettings, _binding: "EngineBinding") -> Any:
    forward_mode = effective.forward_mode
    if forward_mode is None or forward_mode is ForwardMode.DISABLED:
        return NOT_VIOLATED
    return int(forward_mode)


def _detect_profit_in_pips(effective: EffectiveSettings, _binding: "EngineBinding") -> Any:
    return True if effective.profit_in_pips is True else NOT_VIOLATED


def _detect_visual(effective: EffectiveSettings, _binding: "EngineBinding") -> Any:
    return True if effective.visual is True else NOT_VIOLATED


def _detect_cross_currency(effective: EffectiveSettings, binding: "EngineBinding") -> Any:
    """N-11: 口座通貨 ≠ 銘柄の決済通貨。供給が無い場合に検証をスキップしない（K-09）。"""
    currency = effective.currency
    if currency is None or currency == binding.settlement_currency:
        return NOT_VIOLATED
    return currency


#: 起点がバー系列の最終時刻に依存する相対プリセット（N-16 の対象）。
#: `LAST_MONTH` は 2026-09-06 の語彙同期（.doc/mt5_options/ 先月.ini）で追加。
RELATIVE_DATE_PRESETS: "frozenset[DatesPreset]" = frozenset(
    {DatesPreset.LAST_MONTH, DatesPreset.LAST_YEAR}
)


def _detect_relative_preset(effective: EffectiveSettings, _binding: "EngineBinding") -> Any:
    """N-16: 相対プリセット（last year / last month）。起点がバー系列の最終時刻に依存する。"""
    date_range = effective.date_range
    preset = None if date_range is None else date_range.preset
    if preset in RELATIVE_DATE_PRESETS:
        return int(preset)
    return NOT_VIOLATED


# ---------------------------------------------------------------------------
# 宣言表
# ---------------------------------------------------------------------------

#: 宣言表（**評価順**）。ID の並びは基本設計 §4.6 のままである。`RULE_*` の 4 件は
#: `simulator/main/unsupported_run_scope.py` が所有する宣言（run 引数だけで判定できるもの）
#: を織り込んだものであり、値をここへ書き写してはいない。
UNSUPPORTED_RULES: "tuple[UnsupportedRule, ...]" = (
    RULE_UNKNOWN_EA,                        # N-01（合流点側の宣言）
    UnsupportedRule(
        unsupported_id="N-02",
        field="optimization",
        reason="Settings 層からの最適化実行は対象外です（単一パスのみ）",
        detect=_detect_optimization,
        reads=("optimization",),
        ui=UiTrigger(
            keys=("Optimization",),
            mode=UI_TRIGGER_EXCEPT_TOKENS,
            tokens=(format_int_token(OptimizationMode.DISABLED),),
        ),
    ),
    UnsupportedRule(
        # 分割位置そのものは実測で確定した（TBD-03 解消: 1=1/2・2=1/3・3=1/4、
        # `.doc/mt5_options/` 2026-09-06）。残る非対象はフォワード実行そのもの。
        unsupported_id="N-03",
        field="forward_mode",
        reason="フォワードテストの実行はエンジンの対象外です",
        detect=_detect_forward,
        reads=("forward_mode",),
        ui=UiTrigger(
            keys=("ForwardMode",),
            mode=UI_TRIGGER_EXCEPT_TOKENS,
            tokens=(format_int_token(ForwardMode.DISABLED),),
        ),
    ),
    RULE_REAL_TICKS_WITHOUT_STORE,          # N-05（合流点側の宣言）
    UnsupportedRule(
        unsupported_id="N-07",
        field="profit_in_pips",
        reason="pips 建ての集計式が BACKTEST_METRICS.md に定義されていません",
        detect=_detect_profit_in_pips,
        reads=("profit_in_pips",),
        ui=UiTrigger(
            keys=("ProfitInPips",),
            mode=UI_TRIGGER_ON_TOKENS,
            tokens=(format_bool_token(True),),
        ),
    ),
    UnsupportedRule(
        unsupported_id="N-09",
        field="visual",
        reason="テスターのリアルタイム描画は移植対象外です",
        detect=_detect_visual,
        reads=("visual",),
        ui=UiTrigger(
            keys=("Visual",),
            mode=UI_TRIGGER_ON_TOKENS,
            tokens=(format_bool_token(True),),
        ),
    ),
    RULE_MULTI_SYMBOL,                      # N-10（合流点側の宣言）
    UnsupportedRule(
        unsupported_id="N-11",
        field="currency",
        reason="口座通貨と銘柄の決済通貨が異なります（現行エンジンは換算レートを持ちません）",
        detect=_detect_cross_currency,
        reads=("currency", "settlement_currency"),
        # 判定源は束縛の `settlement_currency`＝実行対象データセットの権威値。
        ui=UiTrigger(keys=("Currency",), mode=UI_TRIGGER_OFF_PROFILE),
    ),
    UnsupportedRule(
        unsupported_id="N-15",
        field="date_range",
        reason=(
            "要求した期間窓がエンジンへ適用されていません"
            "（当該 EA のデータ取得経路は marketdata_window を参照しません）"
        ),
        detect=None,  # 判定にはエンジンが返したバー系列が要る（window.verify_window_applied）
        # UI からは判定できない（仕様訂正 2026-08-19・R-10）。当初は「窓を課すのは custom
        # 指定のときだけ」という**必要条件**を `on_presence` で発火条件に用いたが、それは
        # 十分条件ではない。窓が正しく適用されて完走する run（実測: `FromDate`/`ToDate` を
        # 実在範囲で指定した run は exit 0）にも「適用されていません」という**偽の断定**が
        # 常時点灯し、本当の非対象の警告まで無視されるようになる。判定はエンジンが返した
        # バー系列を要するため、生トークンでは判定できない——それを N-10 と同じ形で宣言する。
        ui=UiTrigger(keys=("FromDate", "ToDate"), mode=UI_TRIGGER_NONE),
    ),
    RULE_SPREAD_DEPENDENT_EA_ON_SPREADLESS_DATA,   # N-17（合流点側の宣言）
    UnsupportedRule(
        unsupported_id="N-16",
        field="date_range.preset",
        reason=(
            "相対プリセット（先月・昨年）の起点はバー系列の最終時刻に依存し、"
            "Settings 層はデータを読まないため窓を決定できません"
        ),
        detect=_detect_relative_preset,
        reads=("date_range",),
        tbd="TBD-14",
        ui=UiTrigger(
            keys=("Dates",),
            mode=UI_TRIGGER_ON_TOKENS,
            tokens=tuple(
                format_int_token(preset) for preset in sorted(RELATIVE_DATE_PRESETS)
            ),
        ),
    ),
)

#: ID → 宣言（唯一の索引）。
RULES: "dict[str, UnsupportedRule]" = {
    rule.unsupported_id: rule for rule in UNSUPPORTED_RULES
}

#: 実行要求時に評価する宣言（宣言順）。判定式を持たないものは対象外。
RUN_REQUEST_RULES: "tuple[UnsupportedRule, ...]" = tuple(
    rule for rule in UNSUPPORTED_RULES if rule.detect is not None
)

#: 送出を伴わない非対象（表を欠けさせないための宣言。ここに理由と所在を残す）。
NON_RAISING_RULES: "dict[str, str]" = {
    "N-04": "v1.1 で撤回・欠番（ISSUE-387 裁定。ExecutionMode はパススルー）",
    "N-06": "Model=0 は近似実行（例外なし）。近似である事実は TesterRunMetadata に記録する",
    "N-08": "STAT_* の集計は compute_stats / metrics_spec の責務（責務境界）",
    "N-12": "corpus 外の [Tester] キーはロード時に E-06（検証層 framework/tester_settings）",
    "N-13": "corpus 未出現の列挙値はロード時に E-05（検証層 framework/tester_settings）",
    "N-14": ".set ファイルは Settings タブの形式ではない（範囲外）",
}


# ---------------------------------------------------------------------------
# 適用範囲の分割（ISSUE-525）
# ---------------------------------------------------------------------------
#
# 是正前、本表を適用する関数の呼び手は写像層
# （「`kwargs_mapper.effective_to_interactor_kwargs`」）ただ 1 つだった。したがって写像層を
# 通らない投入経路（「`settings`」 ブロックを持たない投入＝「`run_backtest`」 を直接呼ぶ経路）は
# **宣言の外へ出られた**。是正は「run 自身の引数だけで判定できる規則を、両経路が必ず通る
# 合流点で適用する」ことであり、その規則と適用器は
# `simulator/main/unsupported_run_scope.py` が所有する（上の再輸出）。
#
# 分割は**人手の列挙では行わない**——各規則が読む判定入力（`UnsupportedRule.reads`）と、
# 合流点が解決できる判定入力（`RUN_SCOPE_INPUTS`）の包含で決める。

#: 設定の語彙を読む宣言（写像層で適用する）。`RUN_SCOPE_RULES` との**分割**であり、
#: 重なりも漏れも無い（検定が固定する）。二重に評価しないのは、同じ判定を 2 度発行すると
#: 出力に何も足さずに費用だけが増えるためである（N-17 はデータ実体のヘッダを読む）。
SETTINGS_SCOPE_RULES: "tuple[UnsupportedRule, ...]" = tuple(
    rule for rule in RUN_REQUEST_RULES if rule not in RUN_SCOPE_RULES
)


def apply_unsupported_rules(
    effective: EffectiveSettings, binding: "EngineBinding"
) -> None:
    """設定の語彙を読む非対象判定を宣言順に適用する（違反は最初の 1 件で Fail-Stop）。

    事前条件: ``effective`` は `TesterSettings.effective()` の像（規則 A 適用済み）。
    事後条件: 例外を送出しなければ、`SETTINGS_SCOPE_RULES` のいずれにも該当しない。
    例外: E-07（「`UnsupportedSettingError`」）。

    run 自身の引数だけで判定できる規則（`RUN_SCOPE_RULES`）はここでは評価しない——
    それらは両経路が必ず通る合流点で適用される（ISSUE-525）。ここで併せて評価すると、
    「`settings`」 経路だけが同じ判定を 2 度発行することになる。
    """
    for rule in SETTINGS_SCOPE_RULES:
        assert rule.detect is not None  # RUN_REQUEST_RULES の構築条件（型の絞り込み）
        violation = rule.detect(effective, binding)
        if violation is not NOT_VIOLATED:
            raise_unsupported(rule, value=violation)
