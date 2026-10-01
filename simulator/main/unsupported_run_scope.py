"""保証境界のうち **run 自身の引数だけで判定できる**宣言と、その適用点（ISSUE-525）。

1. 層名/責務:
    main 層（Composition Root）。非対象宣言の**型**（`UnsupportedRule` / `UiTrigger`）と、
    判定入力が run の引数だけで揃う宣言（N-01 / N-05 / N-10 / N-17）を持ち、合流点
    （`simulator.main.build_interactor`）から一括適用する。

2. なぜ `simulator/main/tester_settings/unsupported.py` と分かれているか:
    是正前、保証境界を適用する関数の呼び手は写像層
    （「`kwargs_mapper.effective_to_interactor_kwargs`」）ただ 1 つだった。したがって写像層を
    通らない投入経路（「`settings`」 ブロックを持たない投入＝「`run_backtest`」 を直接呼ぶ経路）は
    **宣言の外へ出られた**。実測（2026-09-25・実 UI・稼働サーバ）: 同じフォームの既定値で、
    `GET /sim/settings-schema` が取れれば N-17 で exit 2、取れなければ受理されて 46 秒以上
    走り続けた。

    是正は「どの経路も必ず通る合流点で適用する」ことである。ところが合流点は
    `simulator.main` に在り、宣言表は子パッケージ `simulator.main.tester_settings` に在った。
    親が子を import すると**パッケージ間の双方向依存**が復活する（子は
    「`ea_input_map`」 / 「`kwargs_mapper`」 / 「`run_from_settings`」 から親を import している）。それは
    ISSUE-502 段階 3 で是正した C-2 そのものであり、
    `simulator/tests/unit/test_package_import_acyclicity.py` が構文木で禁じている
    （関数内 import も同じ辺として数える＝退避では回避できない。実測 2026-09-25: 関数内
    import 版で当該ゲート 2 件が赤になった）。

    よって「どちらでもない第三の点」へ置く——「`engine_data_consistency`」（規則 S）が
    同じ理由で同じ場所に在るのと同型である。**宣言はどこに置いても 1 箇所であり**、
    置き場所を決めるのは「その判定入力を所有するのは誰か」である: run の引数だけで
    判定できる宣言は Composition Root が所有し、設定の語彙を読む宣言は設定層が所有する。

3. 含む構造:
    UiTrigger / UnsupportedRule       : 宣言の型（UI 束縛・非対象 1 件）。
    NOT_VIOLATED / raise_unsupported  : 「違反なし」の番人と送出。
    RUN_SCOPE_INPUTS                  : 合流点が解決できる判定入力 → その出所。
    RunScopeInputs                    : 合流点が運ぶ判定入力の束。
    run_scope_inputs_for              : 仮引数束から判定入力を組む（唯一の組立点）。
    select_run_scope_rules            : 宣言から合流点側の規則を選ぶ（人手の列挙をしない）。
    RUN_SCOPE_DECLARATIONS            : 合流点側の宣言（N-01 / N-05 / N-10 / N-17）。
    apply_run_scope_unsupported_rules : 合流点での一括評価（最初の 1 件で Fail-Stop）。

4. 依存:
    標準: dataclasses / typing
    外部: なし
    プロジェクト内: simulator.adapter.tester_settings.ini_codec（生トークン表記の唯一の宣言）/
                    simulator.domain.exceptions / simulator.domain.tester_settings_exceptions /
                    simulator.main.ea_bindings（列挙 2 つの権威。親を import しないので循環なし）/
                    simulator.usecase.tester_settings（列挙・modelling 語彙の逆写像）/
                    simulator.adapter.repository.ohlc_marketdata_csv（気配幅の供給の実測・
                        N-17 の中でのみ読む）

    **`simulator.main.tester_settings` を import しない**（上記 2 の理由）。

方針（基本設計 §4.6）: 非対象を沈黙スキップしない。非対象設定を実行要求された場合は
例外を送出して run を中止する（Fail-Stop）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, NoReturn

from simulator.adapter.tester_settings.ini_codec import format_int_token
from simulator.domain.exceptions import BacktestError, ConfigError
from simulator.domain.tester_settings_exceptions import UnsupportedSettingError
# 列挙 2 つ（実行可能な EA 名・気配幅を読む EA 名）の**権威**。合流点は Composition Root
# であり、この 2 つの列挙の所有者そのものである（設定層が同じ列挙を運ぶ必要はもう無い）。
# `simulator.main.ea_bindings` は親パッケージ `simulator.main` を import しないため、
# ここから読んでもパッケージ間の循環は生じない（実測: 「`test_package_import_acyclicity`」）。
from simulator.main.ea_bindings import known_ea_names, spread_dependent_ea_names
from simulator.usecase.tester_settings import TICK_MODELS_BY_ENGINE_ID, TickModel

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
            `ini_codec` の公開フォーマッタ（`format_int_token` / 「`format_bool_token`」）を
            通して作る（字形の宣言を書き直さない）。
    """

    keys: "tuple[str, ...]"
    mode: str
    tokens: "tuple[str, ...]" = ()


@dataclass(frozen=True)
class UnsupportedRule:
    """非対象 1 件の宣言。

    unsupported_id: `N-01`〜`N-17`。例外 `context` の ``unsupported_id`` に載る。
             この概念の呼び名は本実装で 1 つだけとする（`UnsupportedSettingError`
             の 「`REQUIRED_CONTEXT`」 が ``unsupported_id`` を語彙として固定しており、
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
    reads:   `detect` が読む**判定入力の名前**（実効設定・注入束のどちらに在るかは問わない。
             名前は 1 つの判定入力を指すため、両者で同名のものは無い）。この宣言があるのは
             「その規則を run 自身の引数だけで判定できるか」を機械で決めるためである
             （`RUN_SCOPE_INPUTS` との包含判定＝`select_run_scope_rules`）。宣言と実装の
             一致は `simulator/tests/unit/test_unsupported_rule_scope_partition.py` が
             `detect` の構文木と突合して固定する（写しが腐れば落ちる）。
    """

    unsupported_id: str
    field: str
    reason: str
    detect: "Callable[[Any, Any], Any] | None" = None
    tbd: "str | None" = None
    build: "Callable[[dict[str, Any]], BacktestError]" = _as_unsupported_setting_error
    ui: "UiTrigger | None" = None
    reads: "tuple[str, ...]" = ()


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
# 合流点が解決できる判定入力
# ---------------------------------------------------------------------------

#: 合流点が解決できる判定入力 → その出所。
#:
#: 値が単純な識別子のものは `simulator.main.build_interactor` の**仮引数名**である
#: （実在することは検定が 「`inspect.signature`」 と突合して固定する）。識別子でないものは
#: 仮引数から導く値であり、散文でその導き方を書く。
#:
#: この表を広げ忘れると何が起きるか: run 引数だけで判定できる規則を新設したのに合流点へ
#: 載らず、「`settings`」 を持たない経路だけが宣言の外へ出る——是正前とまったく同じ穴である。
#: それを人の注意力に任せないため、検定
#: （`simulator/tests/unit/test_unsupported_rule_scope_partition.py`）が
#: 「「`build_interactor`」 の仮引数名だけを読む規則が設定側に居たら落ちる」形で機械的に禁じる。
RUN_SCOPE_INPUTS: "dict[str, str]" = {
    # 判定入力は EA 名（語幹）である。`.ini` の `Expert`（`subject_path`）そのものでは
    # ない——写像層が 「`ea_stem`」 で語幹へ直してから 「`build_interactor`」 へ渡すので、合流点が
    # 見るのは常に語幹だからである。語幹でない値（`.ex5` 付きなど）が届いたときは N-01 が
    # 先に「実行可能な EA 名ではない」として落とす（評価順の先頭＝安全側）。宣言の `field`
    # が `subject_path` のままなのは、非対象なのは**設定のどのフィールドか**を答える欄で
    # あり、判定入力の名前とは別の問いだからである。
    "ea_name": "ea_name",
    "symbol": "symbol",
    "data_path": "data_path",
    "tick_store_root": "tick_store_root",
    # 決定論設定（`config_overrides`）を解決した `tick_model`（エンジン語彙）を
    # `TICK_MODELS_BY_ENGINE_ID` で Settings 層の語彙へ写したもの。
    "tick_model": "決定論設定の tick_model を Settings 層の語彙へ写した値",
    # EA 束縛の公開アクセサ（`simulator.main.known_ea_names`）。合流点は Composition
    # Root であり、この列挙の所有者そのものである。
    "known_ea_names": "EA 束縛の列挙（実行可能な EA 名）",
    # EA 束縛の宣言から導く「気配幅を読む EA」の列挙（`spread_dependent_ea_names`）。
    "spread_dependent_ea_names": "EA 束縛の宣言から導く気配幅依存 EA の列挙",
}


@dataclass(frozen=True)
class RunScopeInputs:
    """合流点が運ぶ判定入力の束（`RUN_SCOPE_INPUTS` と 1:1）。

    `detect` は実効設定と注入束の 2 つを受けるが、本束は**その両方の役として同じ実体を
    渡す**。判定入力の名前は 1 つの事実を指すため、実効設定側・注入束側という区別は
    判定にとって意味を持たない（区別は「値の供給者が誰か」であって、判定の内容ではない）。
    2 つの入れ物を作ると、同じ名前をどちらへ置くかという選択が毎回生じ、置き場所の食い違いが
    沈黙で判定を空振りさせる。

    フィールドの集合が `RUN_SCOPE_INPUTS` の鍵と一致することは検定が固定する
    （運べない入力を「合流点で解決できる」と宣言すると、分割が空手形になる）。
    """

    ea_name: str
    symbol: "str | None"
    data_path: "str | None"
    tick_store_root: "str | None"
    tick_model: "TickModel | None"
    known_ea_names: "frozenset[str]"
    spread_dependent_ea_names: "frozenset[str]"


def run_scope_inputs_for(job: "Mapping[str, Any]", *, tick_model_id: str) -> RunScopeInputs:
    """「`build_interactor`」 の仮引数束から判定入力を組む（**唯一の組立点**）。

    事前条件: ``job`` の鍵が 「`build_interactor`」 の仮引数名であること（本体の
        ``job = dict(locals())`` の像、または写像層が返す投入引数束——どちらも同じ名前で
        ある）。``tick_model_id`` はエンジン語彙の modelling id。
    事後条件: `RUN_SCOPE_INPUTS` の全入力が埋まった束を返す。
    例外: ``KeyError``（宣言した出所の仮引数が ``job`` に無い＝呼出側の組み立て漏れ）。

    引数名は `RUN_SCOPE_INPUTS` の値から引く——**ここで名前を書き写さない**。書き写すと
    宣言（どこから取るか）と実装（実際に取った場所）が 2 箇所になり、片方だけが腐る。

    列挙 2 つは Composition Root の公開アクセサから読む（引数で受けない）。受けると
    呼出側ごとに「どの列挙を渡すか」の選択が生じ、検定が本番と違う列挙で緑になれる。
    """
    supplied = {
        name: job[source]
        for name, source in RUN_SCOPE_INPUTS.items()
        if source.isidentifier()
    }
    return RunScopeInputs(
        tick_model=TICK_MODELS_BY_ENGINE_ID.get(tick_model_id),
        known_ea_names=frozenset(known_ea_names()),
        spread_dependent_ea_names=frozenset(spread_dependent_ea_names()),
        **supplied,
    )


def select_run_scope_rules(
    rules: "tuple[UnsupportedRule, ...]",
) -> "tuple[UnsupportedRule, ...]":
    """合流点で適用する規則を宣言から選ぶ（読む入力が合流点で全部そろうもの）。

    事前条件: 各規則の `reads` が `detect` の実装と一致していること（検定が固定する）。
    事後条件: 戻り値の各規則は `RunScopeInputs` だけで評価できる。
    例外: 送出しない。
    """
    return tuple(
        rule
        for rule in rules
        if rule.detect is not None and set(rule.reads) <= set(RUN_SCOPE_INPUTS)
    )


# ---------------------------------------------------------------------------
# 判定式（run の引数だけで判定できるもの）
# ---------------------------------------------------------------------------


def _detect_unknown_ea(effective: RunScopeInputs, binding: RunScopeInputs) -> Any:
    """N-01: 未登録 EA 名の沈黙フォールバックを上流で遮断する。

    判定源は**注入された** ``binding.known_ea_names`` であり、EA 登録表ではない
    （本モジュールは `simulator.main.ea_bindings` を import しない）。遮断したい下流の
    挙動が `main/ea_bindings` の `_EA_BINDINGS.get(ea_name, 既定)` である、という関係で
    あって、判定源そのものではない。両集合の関係（注入集合 ⊇ 登録キー、差分は既定
    フォールバック EA 名のみ）は `test_unsupported_n01_ea_name_source.py` が固定する。
    """
    name = effective.ea_name
    return NOT_VIOLATED if name in binding.known_ea_names else name


def _detect_real_ticks_without_store(
    effective: RunScopeInputs, binding: RunScopeInputs
) -> Any:
    if effective.tick_model is TickModel.REAL_TICKS and binding.tick_store_root is None:
        return int(TickModel.REAL_TICKS)
    return NOT_VIOLATED


def _detect_multi_symbol(effective: RunScopeInputs, _binding: RunScopeInputs) -> Any:
    """N-10: 実効設定が保持する銘柄が単一の文字列でない場合。

    `.ini` の 「`Symbol`」 は単一値であり（44 / 44 件実測）、複数指定の**表記**は corpus に
    実例が無い。したがって「区切り文字で複数列挙されている」という判定は実証できず、
    発明もしない。ここで固定するのは構造上の不変条件（投入契約が受けるのは
    ``symbol: str`` 1 個）であり、それが崩れた時点で Fail-Stop する。
    """
    symbol = effective.symbol
    if symbol is None or isinstance(symbol, str):
        return NOT_VIOLATED
    return str(symbol)


def _detect_spread_dependent_ea_on_spreadless_data(
    effective: RunScopeInputs, binding: RunScopeInputs
) -> Any:
    """N-17: 気配幅を読む EA × 気配幅の列を供給しないデータ実体。

    気配幅を供給しない系列では spread=0 供給になるため、約定価格式が
    open + spread×point の EA は実 MT5 と一致しない（H-4）。判定はデータ実体の
    ヘッダ実測（`supplies_spread`）で行う——EA 名や拡張子から推測しない。

    **問うのは形式ではなく気配幅の供給そのものである**（ISSUE-511 段階 8-C）。段階 8-B
    までは「形式 == marketdata」で代理していたが、形式は気配幅の代理変数にすぎない。
    代理で測ると、気配幅の列を持つ marketdata 9 列（段階 2 の新系列）まで弾き、
    気配幅を持たない comma 形式は素通しする。列名を知るのは読み手だけでよい——
    本モジュールは「供給するか」の 2 値だけを受け取る。

    判定源の EA 名集合（``binding.spread_dependent_ea_names``）も**注入**である。是正前は
    宣言表に手書きの 3 名が在り、判定の瞬間を ``current_open`` と名乗るのに列挙に無い EA
    （`WeeklyVolBand_EA`）が気配幅なしのデータで完走していた（ISSUE-525 の実測
    2026-09-25: exit=0 / trades=1 / 約定価格＝足の始値）。列挙は EA 束縛の宣言から導く
    （`simulator.main.ea_bindings.spread_dependent_ea_names`）。

    ``data_path is None``（バー系列を供給しない）は**対象外**である。「気配幅の無い
    データで走らせる」ことと「データを 1 行も読まない」ことは別の事実であり、後者では
    本宣言が防ぐ事象（spread=0 供給が実 MT5 と一致しない）が原理的に起こらない。両者が
    同値であることの根拠は規則 S: 本宣言を含む `RUN_SCOPE_DECLARATIONS` の**非テストの
    呼び手は `simulator.main.build_interactor` ただ 1 つ**であり、その関数は規則 S の
    整合検査を**先に**呼ぶ。その検査（`simulator/main/engine_data_consistency.py` へ委譲）は
    ``consumes_market_data(tick_model) != has_data`` を E-03 で Fail-Stop する双条件で
    ある。この 2 点（唯一の入口であること・そこで規則 S が先に効くこと）は
    `simulator/tests/unit/test_unsupported_rules_run_after_rule_s.py` が構文木で固定する。
    したがって本判定に届く ``data_path is None`` は `MATH_CALCULATIONS`（`Model=3`）と
    同値になる。ここを落とすと、完走していた `Model=3` の run が exit 0 から exit 2 へ
    変わる（実測 2026-09-18・本作業ツリー。数え方: 本条件を外した版を一時適用して
    `simulator/sim_ui/tests` を全件走らせ、赤になった検定を数えた——1,219 件中 1 件）。
    """
    from simulator.adapter.repository.ohlc_marketdata_csv import supplies_spread

    name = effective.ea_name
    if name not in binding.spread_dependent_ea_names:
        return NOT_VIOLATED
    if binding.data_path is None:
        # 気配幅の有無とは**別の事実**（バー系列を 1 行も読まない）。連言の 1 項として
        # 畳むと「気配幅が無い」の一種に見えるが、根拠も所有者も違う（規則 S・上記）。
        return NOT_VIOLATED
    if supplies_spread(binding.data_path):
        return NOT_VIOLATED
    return name


# ---------------------------------------------------------------------------
# 宣言（合流点側）
# ---------------------------------------------------------------------------

#: N-01。評価順の**先頭**である（他の宣言より前）。語幹でない EA 名（`.ex5` 付きなど）が
#: 合流点へ届いたとき、まずここが「実行可能な EA 名ではない」として落とす＝安全側に倒れる。
RULE_UNKNOWN_EA = UnsupportedRule(
    unsupported_id="N-01",
    field="subject_path",
    reason=(
        "実行可能な EA は注入された実行可能 EA 名集合（known_ea_names）に限られます"
        "（未登録名の沈黙フォールバックを遮断します）"
    ),
    detect=_detect_unknown_ea,
    reads=("ea_name", "known_ea_names"),
    build=_as_config_error,
    # 候補（配った Expert 一覧＝known_ea_names）に無い値なら当たる。
    ui=UiTrigger(keys=("Expert",), mode=UI_TRIGGER_OFF_CANDIDATES),
)

#: N-05。実ティックの供給元が注入されていない run を拒む。
RULE_REAL_TICKS_WITHOUT_STORE = UnsupportedRule(
    unsupported_id="N-05",
    field="tick_model",
    reason="実ティックの供給元（tick_store_root）が注入されていません",
    detect=_detect_real_ticks_without_store,
    reads=("tick_model", "tick_store_root"),
    ui=UiTrigger(
        keys=("Model",),
        mode=UI_TRIGGER_ON_TOKENS,
        tokens=(format_int_token(TickModel.REAL_TICKS),),
    ),
)

#: N-10。投入契約が受けるのは単一銘柄（``symbol: str``）1 個だけである。
RULE_MULTI_SYMBOL = UnsupportedRule(
    unsupported_id="N-10",
    field="symbol",
    reason="現行エンジンの投入契約は単一銘柄（symbol: str）のみを受けます",
    detect=_detect_multi_symbol,
    reads=("symbol",),
    # 判定は「単一の文字列か」という**構造**であり、`.ini` の生トークンは常に
    # 文字列である。UI の値からは原理的に当たり得ないため発火条件を持たない。
    ui=UiTrigger(keys=("Symbol",), mode=UI_TRIGGER_NONE),
)

#: N-17。気配幅を読む EA × 気配幅を供給しないデータ実体。
RULE_SPREAD_DEPENDENT_EA_ON_SPREADLESS_DATA = UnsupportedRule(
    unsupported_id="N-17",
    field="subject_path",
    reason=(
        "spread 依存 EA（約定式が open + spread×point）は、気配幅の列を供給しない"
        "データセットでは実行できません（spread=0 供給になり実 MT5 と一致しない・H-4）"
    ),
    detect=_detect_spread_dependent_ea_on_spreadless_data,
    reads=("ea_name", "data_path", "spread_dependent_ea_names"),
    # 生トークンだけでは判定できない（データ実体の形式に依存する）——N-10 と同じ形。
    # 投入時の Fail-Stop が本則で、UI へは実行前の 400 で理由が届く。
    ui=UiTrigger(keys=("Expert",), mode=UI_TRIGGER_NONE),
)

#: 合流点側の宣言（**評価順**）。設定層の宣言表はこれを自分の並びへ織り込む。
RUN_SCOPE_DECLARATIONS: "tuple[UnsupportedRule, ...]" = (
    RULE_UNKNOWN_EA,
    RULE_REAL_TICKS_WITHOUT_STORE,
    RULE_MULTI_SYMBOL,
    RULE_SPREAD_DEPENDENT_EA_ON_SPREADLESS_DATA,
)

#: 合流点（「`build_interactor`」）で適用する宣言。宣言から**選ぶ**ことで、上の並びに
#: 合流点で判定できない規則が混ざったら（`reads` が広すぎたら）静かに外れる代わりに
#: 検定が落ちる（分割の全域性を検定が固定する）。
RUN_SCOPE_RULES: "tuple[UnsupportedRule, ...]" = select_run_scope_rules(
    RUN_SCOPE_DECLARATIONS
)


def apply_run_scope_unsupported_rules(inputs: RunScopeInputs) -> None:
    """run 自身の引数だけで判定できる非対象判定を適用する（合流点の 1 箇所）。

    事前条件: ``inputs`` が合流点の実引数から組まれていること。
    事後条件: 例外を送出しなければ、`RUN_SCOPE_RULES` のいずれにも該当しない。
    例外: E-07（`UnsupportedSettingError`）または `ConfigError`（N-01）。

    **どの投入経路もここを通る**ことが本関数の存在理由である。適用点が写像層にしか
    無かったとき、「`settings`」 ブロックを持たない投入は保証境界の外で完走できた。
    """
    for rule in RUN_SCOPE_RULES:
        assert rule.detect is not None  # select_run_scope_rules の構築条件（型の絞り込み）
        violation = rule.detect(inputs, inputs)
        if violation is not NOT_VIOLATED:
            raise_unsupported(rule, value=violation)
