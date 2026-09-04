"""保証境界（非対象）N-01〜N-16 の宣言表と送出（基本設計 §4.6・内部設計 §8.4.4）。

1. 層名/責務:
    main 層（Composition Root）。「本実装が保証しない設定」の**唯一の宣言場所**。
    非対象の ID・対象フィールド・理由・TBD 番号・判定式・送出例外種別を 1 エントリ
    にまとめ、実行要求時に順に評価する。判定の所有者をここ 1 箇所に閉じることで、
    非対象の追加が既存の分岐・関数の書き換えを要さない（OCP）。

2. 含む構造:
    UiTrigger             : 設定フォームへの束縛（効くキー・発火条件・生トークン）。
    UnsupportedRule       : 非対象 1 件の宣言（ID / field / reason / 判定式 / 送出 / UI 束縛）。
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
                    simulator.main.tester_settings.ea_input_map（ea_stem）/
                    simulator.adapter.tester_settings.ini_codec（生トークン表記の唯一の宣言。
                        UI 束縛のトークンを字形ごと書き直さないために公開フォーマッタを使う）

方針（基本設計 §4.6）: 非対象を沈黙スキップしない。非対象設定を実行要求された場合は
例外を送出して run を中止する（Fail-Stop）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, NoReturn

from simulator.adapter.tester_settings.ini_codec import (
    format_bool_token,
    format_int_token,
)
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


#: UI 側の発火条件（`UiTrigger.mode`）。設定フォームは `.ini` の**生トークン**しか持たない
#: ため、判定式（`detect`）をそのまま動かせない。そこで「どのキーの・どういう値なら
#: 当該 rule に当たるか」を宣言として持たせ、UI はこの宣言だけを照合する。
#: キー名の正規表現でフィールド名を再導出したり、既定値との差分を該当の代理にしたりすると、
#: 宣言と食い違っても静かに 0 件（または過剰発火）になる。
UI_TRIGGER_ON_TOKENS = "on_tokens"           #: 列挙した生トークンに一致したら発火
UI_TRIGGER_EXCEPT_TOKENS = "except_tokens"   #: 列挙した生トークン**以外**なら発火
#: そのキーが投入本文に載るなら発火。**現在この形を使う rule は無い**（N-15 は R-10 で
#: `none` へ訂正した）。語彙としては残す——「キーの存在だけで当たる非対象」は表現として
#: 成立し、front も実装済みだからである。使う rule が現れたときに宣言 1 行で足りる。
UI_TRIGGER_ON_PRESENCE = "on_presence"
UI_TRIGGER_OFF_CANDIDATES = "off_candidates" #: 配った候補集合に無い値なら発火
UI_TRIGGER_OFF_PROFILE = "off_profile"       #: 実行対象データセットの権威値と異なれば発火
UI_TRIGGER_NONE = "none"                     #: 生トークンでは判定できない（構造不変条件の防壁）

#: 妥当な発火条件の集合（UI・schema の検定が参照する唯一の宣言）。
UI_TRIGGER_MODES: "frozenset[str]" = frozenset({
    UI_TRIGGER_ON_TOKENS,
    UI_TRIGGER_EXCEPT_TOKENS,
    UI_TRIGGER_ON_PRESENCE,
    UI_TRIGGER_OFF_CANDIDATES,
    UI_TRIGGER_OFF_PROFILE,
    UI_TRIGGER_NONE,
})
#: トークン列挙を伴う条件（空のトークン集合は宣言の書き損じ）。
UI_TRIGGERS_WITH_TOKENS: "frozenset[str]" = frozenset({
    UI_TRIGGER_ON_TOKENS, UI_TRIGGER_EXCEPT_TOKENS,
})


@dataclass(frozen=True)
class UiTrigger:
    """非対象 1 件の**UI 束縛**の宣言（どの `.ini` キーの・どんな値で当たるか）。

    keys:   効く `.ini` キー（標準キー順に実在する名前）。**空にしない**——空は
            「宣言はあるのに UI では絶対に出ない」告知を作り、沈黙で保証境界の外へ
            出られるようにしてしまう。
    mode:   発火条件（``UI_TRIGGER_*`` のいずれか）。
    tokens: ``on_tokens`` / ``except_tokens`` のときの生トークン集合。表記は
            `ini_codec` の公開フォーマッタ（`format_int_token` / `format_bool_token`）を
            通して作る（字形の宣言を書き直さない）。
    """

    keys: "tuple[str, ...]"
    mode: str
    tokens: "tuple[str, ...]" = ()


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
    ui:      設定フォームへの束縛（:class:`UiTrigger`）。投入**前**に理由を出すための
             宣言であり、`detect` と同じものを指す（対応は
             `tests/integration/test_tester_settings_to_interactor.py` が実行段の
             実測で結ぶ）。``None`` は宣言の欠落であり、schema を組む側が Fail-Stop する。
    """

    unsupported_id: str
    field: str
    reason: str
    detect: "Callable[[EffectiveSettings, EngineBinding], Any] | None" = None
    tbd: "str | None" = None
    build: "Callable[[dict[str, Any]], BacktestError]" = _as_unsupported_setting_error
    ui: "UiTrigger | None" = None


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
        # 候補（配った Expert 一覧＝known_ea_names）に無い値なら当たる。
        ui=UiTrigger(keys=("Expert",), mode=UI_TRIGGER_OFF_CANDIDATES),
    ),
    UnsupportedRule(
        unsupported_id="N-02",
        field="optimization",
        reason="Settings 層からの最適化実行は対象外です（単一パスのみ）",
        detect=_detect_optimization,
        ui=UiTrigger(
            keys=("Optimization",),
            mode=UI_TRIGGER_EXCEPT_TOKENS,
            tokens=(format_int_token(OptimizationMode.DISABLED),),
        ),
    ),
    UnsupportedRule(
        unsupported_id="N-03",
        field="forward_mode",
        reason="フォワードの期間分割位置が未確定です",
        detect=_detect_forward,
        tbd="TBD-03",
        ui=UiTrigger(
            keys=("ForwardMode",),
            mode=UI_TRIGGER_EXCEPT_TOKENS,
            tokens=(format_int_token(ForwardMode.DISABLED),),
        ),
    ),
    UnsupportedRule(
        unsupported_id="N-05",
        field="tick_model",
        reason="実ティックの供給元（tick_store_root）が注入されていません",
        detect=_detect_real_ticks_without_store,
        ui=UiTrigger(
            keys=("Model",),
            mode=UI_TRIGGER_ON_TOKENS,
            tokens=(format_int_token(TickModel.REAL_TICKS),),
        ),
    ),
    UnsupportedRule(
        unsupported_id="N-07",
        field="profit_in_pips",
        reason="pips 建ての集計式が BACKTEST_METRICS.md に定義されていません",
        detect=_detect_profit_in_pips,
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
        ui=UiTrigger(
            keys=("Visual",),
            mode=UI_TRIGGER_ON_TOKENS,
            tokens=(format_bool_token(True),),
        ),
    ),
    UnsupportedRule(
        unsupported_id="N-10",
        field="symbol",
        reason="現行エンジンの投入契約は単一銘柄（symbol: str）のみを受けます",
        detect=_detect_multi_symbol,
        # 判定は「単一の文字列か」という**構造**であり、`.ini` の生トークンは常に
        # 文字列である。UI の値からは原理的に当たり得ないため発火条件を持たない。
        ui=UiTrigger(keys=("Symbol",), mode=UI_TRIGGER_NONE),
    ),
    UnsupportedRule(
        unsupported_id="N-11",
        field="currency",
        reason="口座通貨と銘柄の決済通貨が異なります（現行エンジンは換算レートを持ちません）",
        detect=_detect_cross_currency,
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
    UnsupportedRule(
        unsupported_id="N-16",
        field="date_range.preset",
        reason=(
            "直近 1 年の起点はバー系列の最終時刻に依存し、"
            "Settings 層はデータを読まないため窓を決定できません"
        ),
        detect=_detect_last_year,
        tbd="TBD-14",
        ui=UiTrigger(
            keys=("Dates",),
            mode=UI_TRIGGER_ON_TOKENS,
            tokens=(format_int_token(DatesPreset.LAST_YEAR),),
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
