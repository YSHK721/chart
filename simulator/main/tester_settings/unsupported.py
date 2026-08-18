"""保証境界（非対象）N-01〜N-16 の宣言表と送出（基本設計 §4.6・内部設計 §8.4.4）。

1. 層名/責務:
    main 層（Composition Root）。「本実装が保証しない設定」の**唯一の宣言場所**。
    非対象の ID・対象フィールド・理由・TBD 番号・判定式・送出例外種別を 1 エントリ
    にまとめ、実行要求時に順に評価する。判定の所有者をここ 1 箇所に閉じることで、
    非対象の追加が既存の分岐・関数の書き換えを要さない（OCP）。

2. 含む構造:
    UnsupportedRule       : 非対象 1 件の宣言（ID / field / reason / 判定式 / 送出）。
    RULES                 : ID → 宣言（N-01〜N-16 のうち送出を伴うもの）。
    RUN_REQUEST_RULES     : 実行要求時に評価する宣言（評価順）。
    NON_RAISING_RULES     : 送出を伴わない非対象（欠番・近似・責務境界・ロード時）。
    apply_unsupported_rules : 実行要求時の一括評価（違反は最初の 1 件で Fail-Stop）。
    raise_unsupported     : 宣言 1 件から例外を組み立てて送出する（文言を書き写さない）。

3. 元 MQL 対応:
    MT5 Settings タブの各コントロールが表す機能のうち、本移植が再現しないもの
    （最適化・フォワード・visual・pips 建て損益・クロス通貨・実ティック等）。

4. 依存:
    標準: dataclasses / typing
    外部: なし
    プロジェクト内: simulator.domain.exceptions（ConfigError）/
                    simulator.domain.tester_settings_exceptions（UnsupportedSettingError）/
                    simulator.usecase.tester_settings（DTO・列挙）/
                    simulator.main.tester_settings.ea_input_map（ea_stem）

方針（基本設計 §4.6）: 非対象を沈黙スキップしない。非対象設定を実行要求された場合は
例外を送出して run を中止する（Fail-Stop）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, NoReturn

from simulator.domain.exceptions import BacktestError, ConfigError
from simulator.domain.tester_settings_exceptions import UnsupportedSettingError
from simulator.main.tester_settings.ea_input_map import ea_stem
from simulator.usecase.tester_settings import (
    DatesPreset,
    EffectiveSettings,
    ForwardMode,
    OptimizationMode,
    TickModel,
)

if TYPE_CHECKING:  # 型注釈専用（実行時は import しない＝循環回避）
    from simulator.main.tester_settings.kwargs_mapper import EngineBinding

#: 「違反なし」を表す番人（``None`` / ``False`` / ``0`` が正当な違反値になり得るため、
#: 判定式の戻り値に偽値を使わない）。
NOT_VIOLATED: Any = object()


def _as_unsupported_setting_error(payload: "dict[str, Any]") -> BacktestError:
    """E-07（既定）。`context` の語彙検査は例外クラス側が行う。"""
    return UnsupportedSettingError(**payload)


def _as_config_error(payload: "dict[str, Any]") -> BacktestError:
    """N-01 用。基本設計 §4.6 は N-01 の送出例外を `ConfigError` と定めている。"""
    message = (
        f"本実装が対象としない設定です: {payload['unsupported_id']} "
        f"({payload['field']}={payload['value']!r})"
    )
    return ConfigError(message, context=payload)


@dataclass(frozen=True)
class UnsupportedRule:
    """非対象 1 件の宣言。

    unsupported_id: `N-01`〜`N-16`。例外 `context` の ``unsupported_id`` に載る。
             この概念の呼び名は本実装で 1 つだけとする（`UnsupportedSettingError`
             の `REQUIRED_CONTEXT` が ``unsupported_id`` を語彙として固定しており、
             宣言側だけ別名（``id``）にすると同一概念に 2 つの名前が生じる）。
    field:   対象フィールド名（例外 `context` の ``field`` に載る）。
    reason:  非対象である理由（文言の唯一の宣言。送出側で書き写さない）。
    detect:  実行要求時の判定式。違反時は**違反値**を、非違反時は ``NOT_VIOLATED``
             を返す。``None`` は「実行要求時の一括評価では判定しない」ことを表し、
             判定に必要な情報を持つ地点（例: 窓の解決・適用結果の検証）から
             `raise_unsupported` で送出する。
    tbd:     未確定事項番号（あれば `context` の ``tbd`` に載る）。
    build:   宣言と `context` から例外を組み立てる関数（既定は E-07）。
    """

    unsupported_id: str
    field: str
    reason: str
    detect: "Callable[[EffectiveSettings, EngineBinding], Any] | None" = None
    tbd: "str | None" = None
    build: "Callable[[dict[str, Any]], BacktestError]" = _as_unsupported_setting_error


def raise_unsupported(rule: UnsupportedRule, *, value: Any, **context: Any) -> NoReturn:
    """宣言 1 件から例外を組み立てて送出する（ID・文言を呼出側へ書き写さない）。"""
    payload: "dict[str, Any]" = {
        "unsupported_id": rule.unsupported_id,
        "field": rule.field,
        "value": value,
        "reason": rule.reason,
    }
    if rule.tbd is not None:
        payload["tbd"] = rule.tbd
    payload.update(context)
    raise rule.build(payload)


# ---------------------------------------------------------------------------
# 判定式（実行要求時）
# ---------------------------------------------------------------------------


def _detect_unknown_ea(effective: EffectiveSettings, binding: "EngineBinding") -> Any:
    """N-01: 未登録 EA 名の沈黙フォールバックを上流で遮断する。

    判定源は**注入された** ``binding.known_ea_names`` であり、`_EA_FACTORIES` ではない
    （本モジュールは `simulator.main` を import しない）。遮断したい下流の挙動が
    `main/__init__.py` の `_EA_FACTORIES.get(ea_name, 既定)` である、という関係であって、
    判定源そのものではない。両集合の関係（注入集合 ⊇ 登録キー、差分は既定フォールバック
    EA 名のみ）は `test_unsupported_n01_ea_name_source.py` が固定する。
    """
    name = ea_stem(effective.subject_path)
    return NOT_VIOLATED if name in binding.known_ea_names else name


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


def _detect_real_ticks_without_store(
    effective: EffectiveSettings, binding: "EngineBinding"
) -> Any:
    if effective.tick_model is TickModel.REAL_TICKS and binding.tick_store_root is None:
        return int(TickModel.REAL_TICKS)
    return NOT_VIOLATED


def _detect_profit_in_pips(effective: EffectiveSettings, _binding: "EngineBinding") -> Any:
    return True if effective.profit_in_pips is True else NOT_VIOLATED


def _detect_visual(effective: EffectiveSettings, _binding: "EngineBinding") -> Any:
    return True if effective.visual is True else NOT_VIOLATED


def _detect_multi_symbol(effective: EffectiveSettings, _binding: "EngineBinding") -> Any:
    """N-10: 実効設定が保持する銘柄が単一の文字列でない場合。

    `.ini` の `Symbol` は単一値であり（44 / 44 件実測）、複数指定の**表記**は corpus に
    実例が無い。したがって「区切り文字で複数列挙されている」という判定は実証できず、
    発明もしない。ここで固定するのは構造上の不変条件（投入契約が受けるのは
    ``symbol: str`` 1 個）であり、それが崩れた時点で Fail-Stop する。
    """
    symbol = effective.symbol
    if symbol is None or isinstance(symbol, str):
        return NOT_VIOLATED
    return str(symbol)


def _detect_cross_currency(effective: EffectiveSettings, binding: "EngineBinding") -> Any:
    """N-11: 口座通貨 ≠ 銘柄の決済通貨。供給が無い場合に検証をスキップしない（K-09）。"""
    currency = effective.currency
    if currency is None or currency == binding.settlement_currency:
        return NOT_VIOLATED
    return currency


def _detect_last_year(effective: EffectiveSettings, _binding: "EngineBinding") -> Any:
    """N-16: `Dates=2`（last year）。起点がバー系列の最終時刻に依存する。"""
    date_range = effective.date_range
    preset = None if date_range is None else date_range.preset
    if preset is DatesPreset.LAST_YEAR:
        return int(DatesPreset.LAST_YEAR)
    return NOT_VIOLATED


# ---------------------------------------------------------------------------
# 宣言表
# ---------------------------------------------------------------------------

UNSUPPORTED_RULES: "tuple[UnsupportedRule, ...]" = (
    UnsupportedRule(
        unsupported_id="N-01",
        field="subject_path",
        reason=(
            "実行可能な EA は注入された実行可能 EA 名集合（known_ea_names）に限られます"
            "（未登録名の沈黙フォールバックを遮断します）"
        ),
        detect=_detect_unknown_ea,
        build=_as_config_error,
    ),
    UnsupportedRule(
        unsupported_id="N-02",
        field="optimization",
        reason="Settings 層からの最適化実行は対象外です（単一パスのみ）",
        detect=_detect_optimization,
    ),
    UnsupportedRule(
        unsupported_id="N-03",
        field="forward_mode",
        reason="フォワードの期間分割位置が未確定です",
        detect=_detect_forward,
        tbd="TBD-03",
    ),
    UnsupportedRule(
        unsupported_id="N-05",
        field="tick_model",
        reason="実ティックの供給元（tick_store_root）が注入されていません",
        detect=_detect_real_ticks_without_store,
    ),
    UnsupportedRule(
        unsupported_id="N-07",
        field="profit_in_pips",
        reason="pips 建ての集計式が BACKTEST_METRICS.md に定義されていません",
        detect=_detect_profit_in_pips,
    ),
    UnsupportedRule(
        unsupported_id="N-09",
        field="visual",
        reason="テスターのリアルタイム描画は移植対象外です",
        detect=_detect_visual,
    ),
    UnsupportedRule(
        unsupported_id="N-10",
        field="symbol",
        reason="現行エンジンの投入契約は単一銘柄（symbol: str）のみを受けます",
        detect=_detect_multi_symbol,
    ),
    UnsupportedRule(
        unsupported_id="N-11",
        field="currency",
        reason="口座通貨と銘柄の決済通貨が異なります（現行エンジンは換算レートを持ちません）",
        detect=_detect_cross_currency,
    ),
    UnsupportedRule(
        unsupported_id="N-15",
        field="date_range",
        reason=(
            "要求した期間窓がエンジンへ適用されていません"
            "（当該 EA のデータ取得経路は marketdata_window を参照しません）"
        ),
        detect=None,  # 判定にはエンジンが返したバー系列が要る（window.verify_window_applied）
    ),
    UnsupportedRule(
        unsupported_id="N-16",
        field="date_range.preset",
        reason=(
            "直近 1 年の起点はバー系列の最終時刻に依存し、"
            "Settings 層はデータを読まないため窓を決定できません"
        ),
        detect=_detect_last_year,
        tbd="TBD-14",
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


def apply_unsupported_rules(
    effective: EffectiveSettings, binding: "EngineBinding"
) -> None:
    """実行要求時の非対象判定を宣言順に適用する（違反は最初の 1 件で Fail-Stop）。

    事前条件: ``effective`` は `TesterSettings.effective()` の像（規則 A 適用済み）。
    事後条件: 例外を送出しなければ、`RUN_REQUEST_RULES` のいずれにも該当しない。
    例外: E-07（`UnsupportedSettingError`）または `ConfigError`（N-01）。
    """
    for rule in RUN_REQUEST_RULES:
        assert rule.detect is not None  # RUN_REQUEST_RULES の構築条件（型の絞り込み）
        violation = rule.detect(effective, binding)
        if violation is not NOT_VIOLATED:
            raise_unsupported(rule, value=violation)
