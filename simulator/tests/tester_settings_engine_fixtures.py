"""変換層（S-5）・`MATH_CALCULATIONS` 経路（S-6）のテストが共有する組み立て器。

1. 責務:
    `EngineBinding` / 実行可能な `TesterSettings` / 合成 comma CSV を**組み立てる唯一の
    場所**。unit（`test_tester_settings_contract_gate.py`）と integration
    （`test_tester_settings_to_interactor.py` / `test_math_calculations_run.py` /
    `test_tester_window_equivalence.py` / `test_run_from_settings.py`）の双方が
    本モジュールを import する。

    既存の `simulator/tests/unit/tester_settings_synthetic.py`（`.ini` 合成）と
    `tester_settings_corpus.py`（corpus 直読）は**改変しない**（内部設計 §9.4 G-1
    「既存ファイル改変 0 件」）。そこに無い「エンジン投入側の組み立て」だけを本モジュール
    が担い、`.ini` 側の合成は `tester_settings_synthetic` へ委譲する（同じ生成器を
    書き写さない＝プロジェクト規約「同じコードを手書き複製するな」）。

2. 含む構造:
    jp225_symbol_spec / engine_binding          : `EngineBinding`（§6 補助 DTO）の組立
    runnable_expert_mapping / runnable_settings : 保証境界内（§4.6）の Expert 設定
    custom_range_settings                       : `FromDate` / `ToDate` 形式（規則 E）
    write_comma_csv / daily_epochs / utc_midnight : 期間窓検証用の合成 comma CSV

3. 元 MQL 対応:
    なし（テスト用の組み立て器）。銘柄仕様の数値は `SymbolSpecCatalog`（プロジェクトの
    単一ソース）から取得し、本モジュールでは 1 つも再宣言しない。

4. 依存:
    標準: datetime / pathlib / typing
    プロジェクト内: simulator.usecase.models（`SymbolSpec`＝銘柄仕様 8 フィールド）
                    simulator.sim_ui.adapter.symbol_spec_catalog（権威値の出所・テスト限定）
                    simulator.framework.tester_settings（API-03）
                    simulator.main.tester_settings.kwargs_mapper（本フェーズの検証対象）
                    simulator.tests.unit.tester_settings_synthetic（`.ini` 合成の単一ソース）

    ⚠️ 本番の変換層（`simulator/main/tester_settings/*`）は `simulator.sim_ui` を
    import しない（不変条件 I-6・`test_settings_layering_main.py` が AST で固定する）。
    本モジュールは**テスト側**であり、権威値の重複宣言を避けるためにカタログを読む。

本モジュールは**テストではない**（`test` で始まらないため pytest は収集しない）。
"""
from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from simulator.framework.tester_settings import tester_settings_from_mapping
from simulator.main.tester_settings.kwargs_mapper import EngineBinding
from simulator.tests.unit.tester_settings_synthetic import OMIT, synthetic_tester_map
from simulator.usecase.models import SymbolSpec
from simulator.usecase.tester_settings.models import TesterSettings

#: 決済通貨（D-10）。corpus 実測の唯一値 `JPY`（基本設計 §4.7 #13）。
#: `EngineBinding.settlement_currency` は既定値を持たない必須注入のため、テスト側が与える。
SETTLEMENT_CURRENCY: str = "JPY"

#: `_EA_FACTORIES` 未登録でも `SymbolSpecCatalog.ea_names()` に載る既定 TC 経路の名前。
#: 実行可能な EA 名はカタログが権威であり、本モジュールは名前を再宣言しない。
DEFAULT_EA_NAME: str = "TC24051901"

#: `build_interactor` の EA パラメータ 5 キー（`required_backtest_keys()` に含まれるが
#: `EffectiveSettings` にも `EA_INPUT_BINDINGS`（初期空・§4.4.1）にも供給源が無い）。
#: 値は既存の comma 経路結合テスト（`test_composition_marketdata_delegation.py`）と同一。
DEFAULT_EA_PARAMS: dict[str, Any] = {
    "ma_period": 2,
    "ma_method": "sma",
    "lot_size": 1.0,
    "stop_loss_points": 500.0,
    "take_profit_points": 3000.0,
}


def jp225_symbol_spec() -> SymbolSpec:
    """`SymbolSpec`（銘柄仕様）を `SymbolSpecCatalog` の権威値から組む。

    値をテスト側に書き写さない（カタログが単一ソース。値を複製すると片方だけ腐る）。

    **対応表を持たない**: `SymbolSpec` の各フィールドは `RunProfile` に同名で存在する
    ため、`dataclasses.fields` による名前一致で機械的に導出する。同名のものを手書きで
    並べ直すと、フィールドが増減したときに片方だけが腐る（プロジェクト規約「同じコードを
    手書き複製するな」）。同じ流儀の前例が本番側にある
    （`kwargs_mapper._derived_bindings` の `_field_names(SymbolSpec) & allowed`）。

    名前一致という前提そのものは
    `simulator/tests/unit/test_tester_settings_engine_fixtures.py` が固定する
    （包含が崩れたら落ちる）。本関数はその前提の上で動く。
    """
    from simulator.sim_ui.adapter.symbol_spec_catalog import SymbolSpecCatalog

    profile = SymbolSpecCatalog().datasets()[0]
    return SymbolSpec(**{field.name: getattr(profile, field.name) for field in fields(SymbolSpec)})


def catalog_ea_names() -> frozenset[str]:
    """N-01 の判定源（実行可能な EA 名の集合）。カタログが権威。"""
    from simulator.sim_ui.adapter.symbol_spec_catalog import SymbolSpecCatalog

    return frozenset(SymbolSpecCatalog().ea_names())


def engine_binding(
    *,
    symbol_spec: SymbolSpec | None = None,
    symbol: str = "JP225",
    period: str = "Daily",
    data_path: Any = None,
    known_ea_names: Iterable[str] | None = None,
    settlement_currency: str = SETTLEMENT_CURRENCY,
    ea_params: Mapping[str, Any] | None = None,
    **overrides: Any,
) -> EngineBinding:
    """`EngineBinding`（内部設計 §6 補助 DTO）を組む唯一の関数。

    ``data_path`` の既定は ``None``（＝バー系列を供給しない）。規則 S は
    「`MATH_CALCULATIONS` は data is None、他は data is not None」を要求するため、
    通常モードのテストは合成 CSV のパスを明示的に与える。
    """
    return EngineBinding(
        symbol_spec=symbol_spec if symbol_spec is not None else jp225_symbol_spec(),
        symbol=symbol,
        period=period,
        data_path=data_path,
        known_ea_names=(
            frozenset(known_ea_names) if known_ea_names is not None else catalog_ea_names()
        ),
        settlement_currency=settlement_currency,
        ea_params=dict(DEFAULT_EA_PARAMS if ea_params is None else ea_params),
        **overrides,
    )


def runnable_expert_mapping(**overrides: Any) -> dict[str, str]:
    """保証境界（§4.6）の**内側**にある Expert 設定の `[Tester]` マッピング。

    `tester_settings_synthetic.synthetic_tester_map` の Expert 既定は corpus 実測値
    （`ProfitInPips=1` / `Visual=1` / `Expert=TC24051903.ex5`）であり、そのままでは
    N-07（pips 建て）・N-09（visual）・N-01（未登録 EA）で実行が拒否される。
    本関数はその 3 点だけを保証境界の内側へ移した土台を返す（他の値は実測値のまま）。
    """
    base: dict[str, Any] = {
        "Expert": f"{DEFAULT_EA_NAME}.ex5",  # N-01: known_ea_names に載る名前
        "ProfitInPips": "0",                 # N-07: pips 建ては非対象
        "Visual": "0",                       # N-09: visual mode は非対象
    }
    base.update(overrides)
    return synthetic_tester_map("expert", **base)


def runnable_settings(*, inputs: Sequence[str] = (), **overrides: Any) -> TesterSettings:
    """保証境界の内側にある `TesterSettings`（API-03 経由＝`source` 付き）。

    ``inputs`` の既定は空。`EA_INPUT_BINDINGS` は初期空（§4.4.1 確定事項）であり、
    未登録の入力名は `ConfigError` になるため、既定の実行経路は入力行を持たない。
    """
    return tester_settings_from_mapping(runnable_expert_mapping(**overrides), inputs)


def custom_range_settings(from_date: date, to_date: date, **overrides: Any) -> TesterSettings:
    """`FromDate` / `ToDate` 形式（`DateRangeKind.CUSTOM`）の設定（規則 E）。"""
    return runnable_settings(
        Dates=OMIT,
        FromDate=from_date.strftime("%Y.%m.%d"),
        ToDate=to_date.strftime("%Y.%m.%d"),
        **overrides,
    )


def utc_midnight(day: date) -> datetime:
    """``day`` の 00:00Z（UTC aware）。期間窓の境界表現（§8.4.2）。"""
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def daily_epochs(first: date, days: int, *, hour: int = 0) -> tuple[int, ...]:
    """``first`` から ``days`` 日ぶんの epoch 秒（1 日 1 本・UTC 基準）。"""
    start = utc_midnight(first) + timedelta(hours=hour)
    return tuple(int((start + timedelta(days=index)).timestamp()) for index in range(days))


def write_comma_csv(path: Path, times: Sequence[int]) -> Path:
    """comma 形式 OHLC CSV を書き出す（`time` は UNIX 秒 int＝Candle 契約 §2.1）。

    委譲経路（`CsvCandleSource`）は `time` を epoch 秒 int として読むため ISO 文字列は
    使わない。OHLC 値は `domain.Bar.__post_init__` の整合検査を通る単調な合成値。
    """
    rows = ["time,open,high,low,close,volume,spread"]
    for index, epoch in enumerate(times):
        base = 100.0 + index
        rows.append(f"{epoch},{base:.1f},{base + 0.5:.1f},{base - 0.5:.1f},{base + 0.2:.1f},1.0,0")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path
