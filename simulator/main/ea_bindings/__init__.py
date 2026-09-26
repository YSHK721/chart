"""EA 束縛の登録表と選択規則（ISSUE-502 段階 4A・SOLID 精査台帳 2026-09-06 の OCP 高）。

**EA を 1 本足すのに書くもの**:

    1. `simulator/main/ea_bindings/<ea>.py`（EA 側モジュール 1 つ。registry の作り方・
       OHLC リーダ・戦略の生成・戦略へ配るパラメータ名を**その EA が自分で宣言する**）
    2. 下の `_EA_MODULES` へ 1 行（宣言の登録）

是正前は `simulator/main/__init__.py` の中に 8 種の編集点があった（実測 10 行＋1 ファイル）:
import 行 / registry ビルダ / ファクトリ / 登録表 / 構築入力型のフィールド /
EA 構築入口の引数 / build_interactor の引数 / strategy_params の dict リテラル。
このうち **6 つが宣言側へ移り**、Composition Root に残るのは
「build_interactor の公開シグネチャに新しいパラメータが要るとき」だけになった
（そのシグネチャは 3 本番モジュールが inspect.signature で反射する事実上の HTTP
スキーマであり、本段階では 1 文字も変えない＝新パラメータが要る EA だけが 1 行触る）。

規則の所在:
    * 選択（`ea_name` → 束縛。未登録は既定 TC 経路へ）は `select_ea_binding` の 1 箇所。
    * 列挙（実行可能な EA 名）は `known_ea_names` の 1 箇所。
    * 列挙（気配幅を読む EA 名）は `spread_dependent_ea_names` の 1 箇所（ISSUE-525。
      判定は戦略の宣言から導き、名前を書き写さない）。
    * 戦略へ配るパラメータ名の**和**は `strategy_param_names` の 1 箇所。
    3 者は役割が違い、表を引く式（`.get(ea_name, 既定)`）は選択にしか無い
    （`simulator/tests/integration/test_ea_factory_selection_rule.py` が AST で固定する）。
"""
from __future__ import annotations

from typing import Any

from simulator.adapter.execution.tick_model_registry import consumes_market_data
from simulator.main.ea_bindings import (
    dataless,
    ma_slope,
    ma_slope_pending,
    open_then_close_5m,
    pro_fit_band,
    simple_touch_long,
    sma_touch_long,
    stop_entry_probe,
    tc24051901,
    weekly_vol_band,
)
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext

__all__ = [
    "COMMON_STRATEGY_PARAMS",
    "DEFAULT_EA_NAME",
    "EaBinding",
    "EaBuildContext",
    "known_ea_names",
    "select_ea_binding",
    "spread_dependent_ea_names",
    "strategy_param_names",
]

#: 登録 EA モジュール。**EA を 1 本足すときに触るのはこの 1 行だけ**である。
#: 並び順は `strategy_param_names` の宣言順（＝戦略パラメータ dict の並び）を決める。
_EA_MODULES = (
    ma_slope,
    ma_slope_pending,
    stop_entry_probe,
    weekly_vol_band,
    pro_fit_band,
    sma_touch_long,
    simple_touch_long,
    open_then_close_5m,
)

#: ea_name → 束縛の登録表。キーは各 EA モジュールの宣言が持つ（ここで名前を写さない）。
_EA_BINDINGS: "dict[str, EaBinding]" = {
    module.BINDING.name: module.BINDING for module in _EA_MODULES
}

#: 未登録 ea_name が落ちる既定経路の束縛（登録表の**外側**にある唯一の実行可能名）。
_DEFAULT_BINDING = tc24051901.BINDING

#: バー系列を消費しない modelling の構成（ea_name では選ばれない。規則が選ぶ）。
_DATALESS_BINDING = dataless.BINDING

#: 未登録 ea_name が落ちる既定 TC 経路の EA 名。
#:
#: 「実行可能な EA 名」は登録表のキーだけでは表せない——未登録名は既定 TC 経路へ
#: フォールバックするため、この 1 名だけが表の外側にある実行可能名である。名前を
#: 表の所有者（本パッケージ）に置く理由（ISSUE-405 実測）: 従来は
#: `sim_ui/adapter/symbol_spec_catalog._DEFAULT_EA` と
#: `simulator/tests/tester_settings_engine_fixtures.DEFAULT_EA_NAME` に同じ文字列が
#: 写されており、フォールバック先を変えると 2 箇所が同時に腐る配置だった。
DEFAULT_EA_NAME: str = _DEFAULT_BINDING.name

#: すべての EA が読む共通の戦略パラメータ（発注そのものに要る 4 つ）。
#: EA 固有の追加分は各 EA モジュールの `EaBinding.strategy_params` が宣言する。
COMMON_STRATEGY_PARAMS: "tuple[str, ...]" = (
    "lot_size",
    "stop_loss_points",
    "take_profit_points",
    "point_size",
)


def known_ea_names() -> "tuple[str, ...]":
    """実行可能な EA 名を昇順で返す（登録表のキー＋既定 TC 経路の名前）。

    **列挙**であって選択ではない。表を引く式（`.get(ea_name, 既定)`）はここに無く、
    選択規則は `select_ea_binding` の 1 箇所に閉じている（AST 検定が役割分担を固定する）。

    なぜ公開するか（ISSUE-405）: 表示スライス（sim_ui）の実行指示フォームは「どの EA を
    選べるか」の一覧を要る。これが無いと外側が私有名（登録表）を越境 import して
    「登録表のキー集合と既定名の和」という**同じ列挙を書き写す**ことになり、実際に
    そうなっていた（sim_ui の symbol_spec_catalog.ea_names）。

    `tick_model` を要求しない: 「どの EA が実行可能か」は run の modelling に依存しない。
    要求すると呼出側が値を捏造することになる。

    戻り値は決定的順（昇順・重複なし）。
    """
    return tuple(sorted(set(_EA_BINDINGS) | {DEFAULT_EA_NAME}))


def spread_dependent_ea_names() -> "tuple[str, ...]":
    """約定価格が足の気配幅を読む EA 名を昇順で返す（ISSUE-525）。

    事前条件: 各束縛が `EaBinding.strategy_type` を名乗り、その戦略が建値基準を宣言して
        いること（どちらも欠けたら Fail-Stop する＝既定へ倒さない）。
    事後条件: 戻り値は決定的順（昇順・重複なし）で、`known_ea_names` の部分集合。
    例外: 「`EntryPriceBasisDeclarationError`」（宣言が無い／語彙の外）。

    **列挙**であって選択ではない（`known_ea_names` と同じ役割分担）。判定は戦略の宣言
    ただ 1 つから導く——是正前はこの集合が
    `simulator/main/tester_settings/unsupported.py` の**手書きの 3 つの名前**であり、宣言が
    ``current_open`` なのに列挙に無い EA（`WeeklyVolBand_EA`）が気配幅を供給しないデータで
    完走していた（実測 2026-09-25: exit=0 / trades=1 / 約定価格＝足の始値）。

    ``strategy_type`` を読むのは、保証境界が run を**始める前**に効くためである（データを
    読む `build` を呼べない）。`known_ea_names` と同じく既定 TC 経路も覆う——未登録名は
    そこへ落ちるため、表のキーだけでは実行可能名を尽くせない。データを読まない構成
    （`_DATALESS_BINDING`）は `ea_name` では選ばれないので含めない。
    """
    from simulator.usecase.entry_price_basis import (
        basis_consumes_bar_spread,
        declared_entry_price_basis,
    )

    return tuple(sorted(
        binding.name
        for binding in (*_EA_BINDINGS.values(), _DEFAULT_BINDING)
        if basis_consumes_bar_spread(declared_entry_price_basis(binding.strategy_type))
    ))


def strategy_param_names() -> "tuple[str, ...]":
    """戦略へ配る build_interactor 引数名（共通＋全 EA 宣言の和・宣言順・重複なし）。

    **和**である理由（現状の契約を変えない）: 是正前は Composition Root 本体の dict
    リテラルが 14 個すべてを EA によらず配っており、「他戦略は未参照のため無害」と
    明記されていた。ここで EA ごとに絞ると配られる集合が run ごとに変わり、数値ではなく
    run-config の subscript が `KeyError` になる形で挙動が動きうる。宣言へ移すのは
    **名前の所在**であって配り方ではない。

    並び順は `_EA_MODULES` の並び（＝是正前の dict リテラルの並び）に一致する
    （登録表は宣言順に組まれ、dict は挿入順を保つ）。

    登録表を読む 3 番目の関数である。**選択はしない**——選択規則（`.get(ea_name, 既定)`）
    は select_ea_binding にしか無く、AST 検定がその個数を 1 に固定する。表を宣言の
    集まりとして読む点は known_ea_names と同じで、取り出す面（キーか、宣言の
    strategy_params か）だけが違う。
    """
    names: "list[str]" = list(COMMON_STRATEGY_PARAMS)
    for binding in (
        *_EA_BINDINGS.values(),
        _DEFAULT_BINDING,
        _DATALESS_BINDING,
    ):
        for name in binding.strategy_params:
            if name not in names:
                names.append(name)
    return tuple(names)


def select_ea_binding(ea_name: str, *, tick_model: str) -> EaBinding:
    """`(strategy, registry, market_data)` を作る束縛を選ぶ**唯一の判定点**。

    規則は 1 つだけである: **バー系列を消費しない modelling は、データを読まない構成を
    採る**。判定入力は `TickModelSpec.requires_market_data`（レジストリの宣言）であり、
    tick_model の id を列挙しない——新しい modelling が増えても本関数は改変不要
    （既定 ``requires_market_data=True`` によって従来どおり EA 表を引く）。

    `if math` を書かない理由（OCP）: 「math かどうか」は Settings 層の語彙であり
    Composition Root の関心ではない。ここで見るのは「データを消費するか」だけである。

    登録表を**引く式は本関数にしか無い**（🔴-1）。A-1 時点では build_ea_indicators が
    同じ式を生で持ち、data-less 規則を知らないまま既定 TC 経路へ落ちて DataError
    （``data_path=None`` の CSV 読み）になっていた。呼出側は判定入力（`tick_model` id）を
    渡すだけにし、規則の複製を作らない。式の個数は
    `simulator/tests/integration/test_ea_factory_selection_rule.py` が AST で機械的に固定する。
    """
    if not consumes_market_data(tick_model):
        return _DATALESS_BINDING
    return _EA_BINDINGS.get(ea_name, _DEFAULT_BINDING)


def build_ea_components(
    ea_name: str, *, tick_model: str, data_path: Any, params: "dict[str, Any]"
) -> "tuple[Any, Any, Any]":
    """選択した束縛で `(strategy, registry, market_data)` を組む（選択＋構築の 1 手続き）。

    呼出側（Composition Root の 2 つの入口）が「選んでから呼ぶ」を書き写さないための
    呼び口である。判定は `select_ea_binding` の 1 箇所のままである。
    """
    binding = select_ea_binding(ea_name, tick_model=tick_model)
    return binding.build(EaBuildContext(data_path=data_path, params=params))
