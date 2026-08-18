"""`EffectiveSettings` → `build_interactor` キーワード引数の写像（内部設計 §8.1）。

1. 層名/責務:
    main 層（Composition Root）。実効設定を現行の投入契約（`build_interactor` の
    キーワード引数）へ写し、実行要求時の規則 R（必須値の充足）と規則 S（バー系列の
    有無と tick_model の整合）を適用する。非対象判定（N-xx）は
    `unsupported` が所有し、本モジュールはその適用を委譲するだけである（SRP）。
    写像先の**キー表は持たない**——許容キー・必須キーは `build_interactor` の実
    シグネチャ（`composition_root_jobs` の 2 関数）から導出する。

2. 含む構造:
    EngineBinding        : Settings が持たない実行資源の注入束（§6 の補助 DTO）。
    TesterRunMetadata    : 実行メタ情報（近似の有無・inert 一覧＝§8.5）。
    KwargBinding         : 引数 1 個を作る束縛（`.ini` 由来・注入由来）。
    to_interactor_kwargs : API-06。事後条件（許容キー包含・必須キー充足）を自ら検査する。
    build_run_metadata   : 実行メタ情報の組み立て。

3. 元 MQL 対応:
    Settings タブの各コントロール（Symbol / Period / Model / Date / Deposit /
    Currency / Leverage / ExecutionMode）。MT5 はこれらを内部テスターへ直接渡すが、
    本移植では現行エンジンの投入契約へ写す。

4. 依存:
    標準: dataclasses / typing
    外部: なし
    プロジェクト内: simulator.adapter.execution.tick_model_registry（`consumes_market_data`
                    ＝バー系列消費の要否の宣言を読む唯一の関数。規則 S はこの宣言だけを
                    見る）/
                    simulator.domain.exceptions / simulator.domain.tester_settings_exceptions /
                    simulator.usecase.models（SymbolSpec）/ simulator.usecase.tester_settings
                    （DTO・列挙に加え `approximation_reason_for`＝遅延の実証状態の単一
                    ソース。宣言は `ExecutionDelay` と同じ `enums` にあり、本モジュール
                    は判定を持たず読むだけである）/
                    simulator.main.tester_settings.unsupported / .window / .ea_input_map /
                    simulator.main（build_interactor。許容・必須キーの単一ソースは
                    その実シグネチャであり、`interactor_key_sets` が関数内 import で読む）

    `simulator.sim_ui` は import しない（不変条件 I-6）。`sim_ui` の
    `allowed_backtest_keys` / `required_backtest_keys` を呼ぶとパッケージ循環を新設する
    ため、キー集合は `build_interactor` のシグネチャから直接導く（`interactor_key_sets`
    の docstring に理由を記載）。両者の導出が一致することは契約ガード検定が突合する。

非対象判定を写像より前に置く理由（基本設計 §6.2 の不変条件）:
    `build_interactor` の呼出に成功した設定は「実行可能」であることを保証するため。
    判定を後ろに置くと、非対象設定でデータ読込・指標事前計算まで進んでしまう。
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Callable, Mapping

from simulator.adapter.execution.tick_model_registry import consumes_market_data
from simulator.domain.exceptions import ConfigError
from simulator.domain.tester_settings_exceptions import (
    SettingsActivationError,
    SettingsKeyMissingError,
)
from simulator.main.tester_settings.ea_input_map import bind_ea_inputs, ea_stem
from simulator.main.tester_settings.unsupported import apply_unsupported_rules
from simulator.main.tester_settings.window import DataWindow, resolve_data_window
from simulator.usecase.models import SymbolSpec
from simulator.usecase.tester_settings import (
    TICK_MODEL_ENGINE_IDS,
    TIMEFRAME_INI_LABELS,
    EffectiveSettings,
    TesterSettings,
    TickModel,
    approximation_reason_for,
)

#: 実行要求時の規則 ID（基本設計 §4.5.5）。
_RULE_RUNTIME_REQUIRED: str = "R"
_RULE_DATA_CONSISTENCY: str = "S"

#: 建値基準の明示値（§4.5.1・MT5 実走整合の実証値）。既定 "close" のままだと
#: spread 無視の分岐に入り MT5 再現にならないため、Settings 経路は明示指定する。
ENTRY_PRICE_BASIS: str = "current_open"

#: Settings 層の `Math calculations` の語彙。A-1（ISSUE-397）以降はエンジン id でもある
#: ため、文字列を書き写さず `TICK_MODEL_ENGINE_IDS` から読む（単一ソース）。
MATH_CALCULATIONS_WORD: str = TICK_MODEL_ENGINE_IDS[TickModel.MATH_CALCULATIONS]

#: `deposit` が inert（`Math calculations`）のときエンジンへ渡す初期証拠金。
#: 推定値を入れず 0.0 とする（`stats.initial_deposit` に 0.0 が現れることを隠さない）。
INERT_DEPOSIT: float = 0.0


@dataclass(frozen=True)
class EngineBinding:
    """Settings 層が持たない実行資源の注入束（§6 の補助 DTO）。

    symbol_spec:         銘柄仕様（既存 `usecase/models.py` の DTO をそのまま使う。
                         8 フィールドが `build_interactor` の銘柄仕様引数と 1:1）。
    symbol / period / data_path: 実行対象データセットの識別（`.ini` の値との整合を検査する）。
                         ``data_path`` の ``None`` は「バー系列を供給しない」の意であり、
                         規則 S（`MATH_CALCULATIONS` ⇔ バー系列なし）の判定入力になる。
    known_ea_names:      実行可能な EA 名の集合（N-01 の事前検証に使う）。
    settlement_currency: 銘柄の決済通貨（D-10）。**既定値を持たない**——「たぶん JPY」の
                         ような推定を層内に置くと銘柄仕様の単一ソース性が壊れ、通貨不一致の
                         run が沈黙で通る。
    ea_params:           EA 固有引数（`ma_period` / `ma_method` / `lot_size` /
                         `stop_loss_points` / `take_profit_points` 等）。**既定値を持たない**
                         必須注入。`.ini` の `[TesterInputs]` から供給できないもの
                         （束縛表が空＝D-02）を呼出側が供給する。推測値を層内で発明しない。
    initial_deposit:     `deposit` が inert のときエンジンへ渡す初期証拠金（ISSUE-397 裁定:
                         inert なフィールドは binding が権威）。既定 `INERT_DEPOSIT`＝0.0。
                         `.ini` の `Deposit` が有効な実行では**参照しない**（.ini が権威）。
    stop_out_level:      現行既定 0.0（`build_interactor` の既定値＝実測）。
    tick_store_root:     実ティック格納根（`REAL_TICKS` 用。未供給時は N-05 で拒否）。
    config_overrides:    データセット側が権威として持つ決定論設定（`entry_price_basis` 等）。

    `RunProfile`（`sim_ui`）を受けない理由: `simulator/main` から `simulator/sim_ui` への
    参照は 0 件（実測）であり、逆向き（`sim_ui/main/run_job.py` → `simulator.main`）が実在
    する。`RunProfile` を持つとパッケージ循環を新設するため、変換は `sim_ui` 側の責務とする。
    """

    symbol_spec: SymbolSpec
    symbol: str
    period: str
    data_path: "str | None"
    known_ea_names: "frozenset[str]"
    settlement_currency: str
    ea_params: "Mapping[str, Any]"
    stop_out_level: float = 0.0
    tick_store_root: "str | None" = None
    config_overrides: "dict | None" = None
    initial_deposit: float = INERT_DEPOSIT


@dataclass(frozen=True)
class TesterRunMetadata:
    """実行メタ情報（§8.5）。Settings 層の語彙を結果と併せて呼出側へ伝える。

    A-1（ISSUE-397）以降、`BacktestConfig.tick_model` は `MATH_CALCULATIONS` でも
    Settings 層の語彙（``"math_calculations"``）と一致する（L-5 の解消）。それでも本 DTO が
    `tick_model` を載せ続けるのは、近似実行（N-06）・inert フィールド一覧と併せて
    「どの設定で走ったか」を呼出側が結果と同じ戻り値から読めるようにするためである。
    """

    tick_model: str
    approximate: bool
    approximation_reasons: "tuple[str, ...]"
    execution_delay: "int | None"
    inert_fields: "tuple[str, ...]"


def tick_model_word(tick_model: TickModel) -> str:
    """Settings 層の tick_model 語彙を返す（メタ情報・診断用）。

    A-1 以降 `TICK_MODEL_ENGINE_IDS` は `TickModel` の全値を覆う（契約ガードが固定）。
    未登録値は**沈黙で既定へ落とさず** `ConfigError` にする（語彙の取り違えを隠さない）。
    """
    word = TICK_MODEL_ENGINE_IDS.get(tick_model)
    if word is None:
        raise ConfigError(
            f"エンジン id を持たない tick_model です: {tick_model!r}",
            context={"tick_model": str(tick_model), "known": sorted(TICK_MODEL_ENGINE_IDS.values())},
        )
    return word


def build_run_metadata(effective: EffectiveSettings) -> TesterRunMetadata:
    """実行メタ情報を組み立てる（近似の理由を列挙する。沈黙しない）。"""
    reasons: list[str] = []
    if effective.tick_model is TickModel.EVERY_TICK:
        reasons.append("N-06")
    delay = effective.execution_delay
    # 遅延の実証状態は本モジュールで判定しない（`execution_delay_evidence` が単一ソース）。
    delay_reason = approximation_reason_for(delay)
    if delay_reason is not None:
        reasons.append(delay_reason)
    return TesterRunMetadata(
        tick_model=tick_model_word(effective.tick_model),
        approximate=bool(reasons),
        approximation_reasons=tuple(reasons),
        execution_delay=delay,
        inert_fields=effective.inert_fields,
    )


def interactor_key_sets() -> "tuple[frozenset[str], frozenset[str]]":
    """`build_interactor` の実シグネチャから (許容キー, 必須キー) を導く。

    単一ソースは関数そのものであり、手書きのキー表は持たない。
    `sim_ui` の `allowed_backtest_keys` / `required_backtest_keys` を呼ばない理由:
    `simulator/main` から `simulator/sim_ui` への参照は 0 件であり、逆向きが実在する
    ため、呼ぶとパッケージ循環を新設する（不変条件 I-6）。同関数群が除く
    `_INJECTED_ONLY_KEYS`（`strategy_decorator` / `strategy_override`）は「HTTP 境界で
    JSON スカラーとして渡せない」という `sim_ui` 固有の制約であり、Python 呼出である
    本層には適用されない。両者の導出が一致することは契約ガード検定が突合する。
    """
    import inspect

    from simulator.main import build_interactor

    params = inspect.signature(build_interactor).parameters
    allowed = frozenset(params)
    required = frozenset(
        name for name, param in params.items() if param.default is inspect.Parameter.empty
    )
    return allowed, required


def verify_engine_data_consistency(*, tick_model: str, has_data: bool) -> None:
    """規則 S の**唯一の判定点**（エンジン語彙＝`tick_model` id だけで判定する）。

    要否の宣言は `TickModelSpec.requires_market_data` の 1 箇所にしかなく、本関数は
    `consumes_market_data` 経由でそれを読むだけである（`if math` を持たない＝OCP。
    新しい modelling が増えても本関数は改変不要）。取り違え——バー系列を消費しない
    modelling にデータを与える／消費する modelling に与えない——を E-03 で Fail-Stop する。

    Settings 語彙を持たない呼出側（`build_interactor`。`config_overrides` を素通しで
    受ける投入経路＝`POST /sim/jobs` → `run_backtest` の実体）が同じ判定へ到達できる
    ように、入力を `EffectiveSettings` ではなく `tick_model` id にしている。判定を
    そちらへ写して二重化しないための形である（🟡-1）。

    `SettingsActivationError` は `ConfigError` の派生であり、終了コード翻訳
    （`adapter.exit_codes`）でそのまま 2 になる。
    """
    if consumes_market_data(tick_model) != has_data:
        raise SettingsActivationError(
            field="tick_model",
            rule_id=_RULE_DATA_CONSISTENCY,
            tick_model=tick_model,
            has_data=has_data,
        )


def verify_data_consistency(effective: EffectiveSettings, *, has_data: bool) -> None:
    """規則 S: バー系列の有無と `tick_model` の整合を検査する（基本設計 §4.5.5）。

    Settings 層の入口。実効設定を**エンジン語彙へ写してから**
    `verify_engine_data_consistency` へ委譲するだけで、判定そのものは持たない
    （`is_math_calculations` を見ていた従来形は、要否の宣言をレジストリと二重に
    持つ形だった）。この検査があるため、写像の事後条件は注入束由来の ``None`` を
    欠落として扱わなくてよい（`_verify_postconditions` の ``injected`` 参照）。
    """
    verify_engine_data_consistency(
        tick_model=tick_model_word(effective.tick_model), has_data=has_data
    )


# ---------------------------------------------------------------------------
# 写像（§8.1 の写像表）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MappingContext:
    """写像 1 回分の入力（各束縛はこの文脈だけを見る）。"""

    effective: EffectiveSettings
    binding: EngineBinding
    window: DataWindow
    ea_name: str


@dataclass(frozen=True)
class KwargBinding:
    """`build_interactor` の引数 1 個を作る束縛。

    ``source`` は診断用の由来名（規則 R 違反の報告に使う）。``extract`` は ``None`` を
    返してよく、その場合「値が供給されていない」ことを意味する。
    """

    param: str
    source: str
    extract: "Callable[[_MappingContext], Any]"
    injected: bool = False
    """``EngineBinding`` のフィールドを直接読む束縛か。

    ``True`` の束縛が返す ``None`` は「供給しない」という**呼出側の宣言**であり、規則 R
    （必須値の充足）の欠落ではない（`EngineBinding` の当該フィールドは既定を持たないため、
    ``None`` は省略ではなく明示指定である）。`.ini` 由来の値は逆に、``None``＝設定に
    書かれていないことを意味するため規則 R の対象になる。
    """


def _require_match(*, param: str, settings_value: Any, injected: Any) -> Any:
    """`.ini` の値と注入された権威値の一致を要求する（沈黙上書きしない・§8.1）。"""
    if settings_value is None:
        return None
    if settings_value != injected:
        raise ConfigError(
            f"{param} が実行対象データセットと一致しません: {settings_value!r} != {injected!r}",
            context={"param": param, "settings": settings_value, "injected": injected},
        )
    return settings_value


def _settings_or_binding(
    ctx: _MappingContext, *, field: str, param: str, settings_value: Any, injected: Any
) -> Any:
    """inert なフィールドは binding の値を採り、それ以外は `.ini` との一致を要求する。

    規則（A-1・ISSUE-397 裁定）: **inert は「`.ini` の値を参照しない」であって
    「エンジンに値が無い」ではない**。エンジン識別子（`symbol` / `period`）と
    `initial_deposit` / `leverage` は、inert のとき `EngineBinding` が権威になる。

    判定源は `EffectiveSettings.inert_fields`（宣言の単一ソース）だけであり、
    `tick_model` を見ない。したがって inert 対象が増減しても本層は改変不要であり、
    `if math` の分岐も生じない（OCP）。
    """
    if field in ctx.effective.inert_fields:
        return injected
    return _require_match(param=param, settings_value=settings_value, injected=injected)


def _symbol(ctx: _MappingContext) -> Any:
    return _settings_or_binding(
        ctx,
        field="symbol",
        param="symbol",
        settings_value=ctx.effective.symbol,
        injected=ctx.binding.symbol,
    )


def _period(ctx: _MappingContext) -> Any:
    """`Period`。`.ini` ラベル（写像は enums の単一ソース）を渡し、整合を検査する。"""
    timeframe = ctx.effective.timeframe
    label = None if timeframe is None else TIMEFRAME_INI_LABELS[timeframe]
    return _settings_or_binding(
        ctx,
        field="timeframe",
        param="period",
        settings_value=label,
        injected=ctx.binding.period,
    )


def _leverage(ctx: _MappingContext) -> Any:
    injected = float(ctx.binding.symbol_spec.leverage)
    if "leverage" in ctx.effective.inert_fields:
        return injected
    leverage = ctx.effective.leverage
    if leverage is None:
        return None
    return _require_match(param="leverage", settings_value=float(leverage), injected=injected)


def _data_path(ctx: _MappingContext) -> Any:
    return ctx.binding.data_path


def _ea_name(ctx: _MappingContext) -> Any:
    return ctx.ea_name


def _initial_deposit(ctx: _MappingContext) -> Any:
    """`Deposit`。inert のときは binding の値を採る（ISSUE-397 裁定・推定値を作らない）。"""
    if "deposit" in ctx.effective.inert_fields:
        return float(ctx.binding.initial_deposit)
    deposit = ctx.effective.deposit
    return None if deposit is None else float(deposit)


def _config_overrides(ctx: _MappingContext) -> Any:
    """決定論 config の上書き（`load_config` の pydantic 検証を通る値のみ）。

    優先順位:
        1. `binding.config_overrides`（データセット側が権威＝`SymbolSpecCatalog` 由来）。
        2. `Model`（Settings の権威項目）→ ``tick_model``（Settings が上書きする）。
        3. ``entry_price_basis`` は未指定時のみ明示値を補う（§4.5.1）。
    """
    overrides = dict(ctx.binding.config_overrides or {})
    # A-1（L-5 の解消）: `Model` は全値がエンジン id を持つ（`tick_model_word` が単一の
    # 取得点で、未登録値は ConfigError）。これにより `BacktestConfig.tick_model` が
    # Settings 層の語彙と一致する（従来 math は既定 "every_tick" のままだった）。
    overrides["tick_model"] = tick_model_word(ctx.effective.tick_model)
    overrides.setdefault("entry_price_basis", ENTRY_PRICE_BASIS)
    return overrides


def _stop_out_level(ctx: _MappingContext) -> Any:
    return ctx.binding.stop_out_level


def _tick_store_root(ctx: _MappingContext) -> Any:
    return ctx.binding.tick_store_root


#: `.ini` 由来・注入由来の値から作る引数の束縛表（§8.1）。銘柄仕様と取得窓は
#: `SymbolSpec` / `DataWindow` の**フィールド名と引数名の一致**から導出する
#: （下の `_derived_bindings`）。同じ名前の表を二重に書かない。
EXPLICIT_BINDINGS: "tuple[KwargBinding, ...]" = (
    KwargBinding("data_path", "binding.data_path", _data_path, injected=True),
    KwargBinding("symbol", "symbol", _symbol),
    KwargBinding("period", "timeframe", _period),
    KwargBinding("ea_name", "subject_path", _ea_name),
    KwargBinding("initial_deposit", "deposit", _initial_deposit),
    KwargBinding("leverage", "leverage", _leverage),
    KwargBinding("config_overrides", "tick_model", _config_overrides),
    KwargBinding("stop_out_level", "binding.stop_out_level", _stop_out_level, injected=True),
    KwargBinding("tick_store_root", "binding.tick_store_root", _tick_store_root, injected=True),
)

_EXPLICIT_PARAMS: "frozenset[str]" = frozenset(b.param for b in EXPLICIT_BINDINGS)


def _field_names(dataclass_type: Any) -> "frozenset[str]":
    return frozenset(field.name for field in fields(dataclass_type))


def _symbol_spec_getter(name: str) -> "Callable[[_MappingContext], Any]":
    def extract(ctx: _MappingContext) -> Any:
        return getattr(ctx.binding.symbol_spec, name)

    return extract


def _window_getter(name: str) -> "Callable[[_MappingContext], Any]":
    def extract(ctx: _MappingContext) -> Any:
        return getattr(ctx.window, name)

    return extract


def _derived_bindings(allowed: "frozenset[str]") -> "tuple[KwargBinding, ...]":
    """名前一致で導出する束縛（銘柄仕様 7 キー・取得窓 4 キー）。

    `SymbolSpec` / `DataWindow` のフィールド名は `build_interactor` の引数名と同名で
    ある（実測）。同名であるものを機械的に対応付けることで、引数が増減しても本
    モジュールに手書きの名前表が残らない（取り残しが起きない）。
    """
    spec_params = sorted((_field_names(SymbolSpec) & allowed) - _EXPLICIT_PARAMS)
    window_params = sorted((_field_names(DataWindow) & allowed) - _EXPLICIT_PARAMS)
    return tuple(
        [
            KwargBinding(name, f"symbol_spec.{name}", _symbol_spec_getter(name))
            for name in spec_params
        ]
        + [
            KwargBinding(name, f"window.{name}", _window_getter(name))
            for name in window_params
        ]
    )


def to_interactor_kwargs(
    settings: TesterSettings, binding: EngineBinding
) -> "dict[str, Any]":
    """API-06: `TesterSettings` → `build_interactor(**kwargs)` の引数辞書。

    事前条件: ``binding`` の各値が供給済み（決済通貨・EA 固有引数は必須注入）。
    事後条件: 返る dict のキー集合は `allowed_backtest_keys()` に含まれ、
        `required_backtest_keys()` をすべて含み、必須キーの値が ``None`` でない
        （注入束が明示的に ``None`` を宣言した引数を除く＝`KwargBinding.injected`）。
    例外: E-03（規則 S）/ E-07（N-02/03/05/07/09/10/11/16）/ E-08（規則 R）/
        `ConfigError`（N-01・データセット不整合・EA 入力の未束縛・不正な `ea_params`）。

    処理順は基本設計 §6.2 の不変条件に従う: 実効設定の導出 → 非対象判定 → 写像。
    """
    return effective_to_interactor_kwargs(settings.effective(), binding)


def effective_to_interactor_kwargs(
    effective: EffectiveSettings, binding: EngineBinding
) -> "dict[str, Any]":
    """API-06 の実体（実効設定を入力に取る形）。

    `TesterSettings` ではなく `EffectiveSettings` を受けるのは、実行 facade が
    メタ情報の組立と警告出力のために既に `effective()` を持っており、そこから
    もう一度 `settings.effective()` を計算し直さないためである（同じ導出を 2 度
    書かない／2 度走らせない）。`to_interactor_kwargs` は本関数の薄い入口である。
    """
    allowed, required = interactor_key_sets()

    verify_data_consistency(effective, has_data=binding.data_path is not None)
    apply_unsupported_rules(effective, binding)

    context = _MappingContext(
        effective=effective,
        binding=binding,
        window=resolve_data_window(effective),
        ea_name=ea_stem(effective.subject_path),
    )
    bindings = EXPLICIT_BINDINGS + _derived_bindings(allowed)

    kwargs: dict[str, Any] = {b.param: b.extract(context) for b in bindings}
    sources = {b.param: b.source for b in bindings}

    # EA 固有引数（`.ini` からは供給できないもの）。本層が供給する引数との衝突は
    # 責務の取り違えであり沈黙させない。
    for param, value in _accepted_ea_params(binding, allowed=allowed, own=set(kwargs)).items():
        kwargs[param] = value
        sources[param] = f"binding.ea_params.{param}"

    # `[TesterInputs]` 由来の型付き引数（`.ini` の指定が最も具体的なので最後に適用する）。
    # 未束縛の入力名は ConfigError（沈黙破棄しない・§6.2）。
    for param, value in bind_ea_inputs(context.ea_name, effective.inputs).items():
        kwargs[param] = value
        sources[param] = f"inputs.{param}"

    _verify_postconditions(
        kwargs,
        sources,
        allowed=allowed,
        required=required,
        injected={b.param for b in bindings if b.injected},
    )
    return kwargs


def _accepted_ea_params(
    binding: EngineBinding, *, allowed: "frozenset[str]", own: "set[str]"
) -> "dict[str, Any]":
    """注入された EA 固有引数を検査して返す（推測で補完しない）。

    - `build_interactor` が受け付けないキー → `ConfigError`（タイポを沈黙させない）。
    - 本層が `.ini` から供給する引数と重なるキー → `ConfigError`（権威の二重化を防ぐ）。
    """
    params = dict(binding.ea_params)
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ConfigError(
            f"build_interactor が受け付けない ea_params です: {', '.join(unknown)}",
            context={"unknown": unknown, "allowed": sorted(allowed)},
        )
    conflicting = sorted(set(params) & own)
    if conflicting:
        raise ConfigError(
            f"ea_params が Settings 由来の引数と衝突しています: {', '.join(conflicting)}",
            context={"conflicting": conflicting},
        )
    return params


def _verify_postconditions(
    kwargs: "dict[str, Any]",
    sources: "dict[str, str]",
    *,
    allowed: "frozenset[str]",
    required: "frozenset[str]",
    injected: "set[str]",
) -> None:
    """事後条件を自ら検査する（§6・キー表を手書きしないことの担保）。

    - 許容外キー: 本モジュールの束縛表の誤り（設定内容では起こり得ない）。握り潰さない
      よう `RuntimeError` で即時に落とす。
    - 必須キーの欠落・``None``: 規則 R 違反（実行要求時に値が供給されていない）。設定
      内容・注入内容に起因するため `SettingsKeyMissingError`（E-08 → 終了コード 2）。

    ``injected`` の ``None`` を欠落として扱わない根拠（A-1・ISSUE-397）:
        `EngineBinding` は当該フィールドに既定を持たない（呼出側が必ず明示する）ため、
        ``None`` は「省略」ではなく「供給しない」という宣言である。バー系列を供給しない
        実行（`Math calculations`）の ``data_path`` がこれに当たる。取り違え——バー系列を
        伴う `tick_model` に ``data_path=None`` を与える／その逆——は本関数より前段の
        規則 S（`verify_data_consistency`）が E-03 で Fail-Stop 済みであり、ここで
        重ねて検査しない（判定の二重化を作らない）。キーの**存在**は引き続き必須である。
    """
    extra = sorted(set(kwargs) - allowed)
    if extra:
        raise RuntimeError(
            f"build_interactor が受け付けない引数を生成しました: {', '.join(extra)}"
        )
    missing = sorted(
        name
        for name in required
        if name not in kwargs or (kwargs[name] is None and name not in injected)
    )
    if missing:
        raise SettingsKeyMissingError(
            keys=tuple(sources.get(name, name) for name in missing),
            rule_id=_RULE_RUNTIME_REQUIRED,
            fields=tuple(missing),
        )
