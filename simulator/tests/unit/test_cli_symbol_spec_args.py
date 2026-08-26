"""検出ゲート: 本番 CLI 3 件の銘柄仕様が argparse 既定値ではなく供給元から来る。

由来: ISSUE-445 RC-1 の**本番残渣（最後の 1 件）**の是正（2026-08-26）。段階 2〜3-E2 と
`b440a9d`（本番ツール 2 件）で権威を供給元スナップショットへ移したが、実行入口である

    - ``simulator/tools/run_is_oos_cli.py``
    - ``simulator/tools/optimize_cli.py``
    - ``simulator/tools/walk_forward_cli.py``

は銘柄仕様 8 項目すべてを argparse の**既定値**として持っていた
（``--contract-size 10.0`` / ``--volume-min 0.01`` / … ）。既定値は「人が書いた値が権威に
なる」形そのものであり、既定のまま実行すると損益が約 10 倍ずれた結果が**無言で**出る。

固定する不変条件:

    1. 3 CLI のパーサが銘柄仕様 8 項目に既定値を持たない（``get_default(...) is None``）。
       ソース上の形（AST）ではなく**組み上がったパーサ**を見るため、既定値をどの経路で
       与えても検出する。
    2. 既定 argv（``--symbol`` も既定）を解決した結果が供給元スナップショットと一致する。
       **期待値をテスト側にリテラルで書かない**（比較相手は ``load_spec_fields`` が引く）。
    3. 明示指定は供給元に優先する（呼出時の意図であり、コマンド行に見える）。ただし供給元と
       食い違えば警告が出る（無言で 10 倍ずれた結果を出さない＝ISSUE-445 の失敗モード）。
    4. 供給元にスナップショットが無い銘柄で明示指定も無ければ **fail-loud**。メッセージは
       「どの銘柄か」「どう取得するか」「どう明示指定するか」を含む。
    5. 8 項目すべてを明示指定した場合はスナップショット不在でも通る（明示指定が唯一の源）。
    6. EA 入力 ``--lot-size`` も既定値を持たず、未指定なら**解決済み仕様の** ``volume_min``
       （＝供給元の最小発注単位）から来る（2026-08-26 追加・下記）。

**6 の由来（2026-08-26・依頼者裁定）**: `--lot-size` だけが人の書いた数（0.1）を既定値として
残っていた。素通し戦略（``TC24051901``・原典 ``.mq5`` を持たず ``NormalizeLot`` 相当が無い）
では供給元の ``volume_min=1.0`` の下で ``InvalidPriceError`` になり実行できない（実測）。
前例は ``export_trade_markers``（``b440a9d``）の ``lot_size=spec["volume_min"]`` であり、
「人が選んだ数ではなく原典 EA の ``NormalizeLot(0.1)`` の戻り値と同値」であることを根拠に
既に採用・レビュー通過している。同じ作法へ揃える。

``--lot-size`` の**明示指定は 8 項目と対称**（明示が優先）だが、**食い違い警告は置かない**
（意図的な非対称）。供給元は ``lot_size`` という値を持たず、``volume_min`` は lot の**下限**で
あって lot の供給値ではない。``volume_min`` と違う lot は正当な指定（例: 2 ロット）であり、
警告にすると正当な使い方のたびに鳴る＝誤りを識別できない。識別できる条件（下限割れ・刻み
外れ）は ``domain.order.Order.validate`` が既に所有し ``InvalidPriceError`` で落ちる
（実測 2026-08-26: ``--lot-size 0.1`` → 範囲外 / ``--lot-size 1.5`` → 刻み外れ）。同じ規則を
CLI 側へ書き写すと所有者が 2 つになる。

負の対照を各検定に対で置く（落ちないゲートは無価値であるため）。
"""
from __future__ import annotations

import pytest

from marketdata.symbol_spec_snapshot import (
    OANDA_JAPAN_MT5_LIVE,
    SPEC_FIELD_SOURCES,
    load_spec_fields,
    snapshot_path,
)
from simulator.tools import optimize_cli, run_is_oos_cli, walk_forward_cli
from simulator.tools.symbol_spec_args import (
    SPEC_KEYS,
    SymbolSpecArgsError,
    resolve_lot_size,
    resolve_symbol_spec,
    spec_option,
)

#: 供給元に登録済みの銘柄（スナップショットが実在する）。値はここに 1 つも書かない。
_SYMBOL = "JP225"
#: 供給元に登録の無い銘柄（fail-loud 経路の入力）。実在しないことは下の検定が実証する。
_UNREGISTERED = "NO_SUCH_SYMBOL"

#: 是正前に 3 CLI が argparse 既定値として持っていた値のうち、供給元と**食い違っていた**もの。
#: 期待値ではなく負の対照（「一致検定が空虚でない」ことの実証）として持つ。撤去済みのため
#: ソースからは取得できず、ここが唯一の記録である。
_REMOVED_DEFAULTS = {
    "contract_size": 10.0,
    "volume_min": 0.01,
    "volume_max": 100.0,
    "volume_step": 0.01,
    "stops_level": 0,
}

#: EA 入力 lot のフィールド名（オプション名は ``spec_option`` が導出する＝綴りを写さない）。
_LOT = "lot_size"
#: 是正前に 3 CLI が ``--lot-size`` の argparse 既定値として持っていた値（2026-08-26 に撤去）。
#: 期待値ではなく負の対照。撤去済みのためソースからは取得できず、ここが唯一の記録である。
_REMOVED_LOT_DEFAULT = 0.1


def _other_lot(spec: dict) -> float:
    """既定（``volume_min``）と**別物**の、供給元の刻みに載る lot（明示指定の入力）。

    値をここに書かない。``volume_step`` が 0 の供給元では既定と一致してしまうが、その場合は
    対の負の対照（``test_the_explicit_lot_used_above_differs_…``）が赤になる（空虚化しない）。
    """
    return spec["volume_min"] + spec["volume_step"]


def _run_is_oos_argv() -> "list[str]":
    return [
        "--data-path", "/nonexistent.csv", "--ea-name", "StopEntryProbe_EA",
        "--split", "2026-04-15", "--is-trading-start", "2026-04-01",
        "--out-dir", "out_is_oos",
    ]


def _optimize_argv() -> "list[str]":
    return _run_is_oos_argv() + [
        "--search-algo", "grid", "--max-candidates", "10", "--objective", "net",
    ]


def _walk_forward_argv() -> "list[str]":
    return [
        "--data-path", "/nonexistent.csv", "--ea-name", "StopEntryProbe_EA",
        "--out-dir", "out_wf",
        "--search-algo", "grid", "--max-candidates", "10", "--objective", "net",
        "--mode", "rolling", "--global-start", "2026-04-01",
        "--global-end", "2026-04-29", "--is-span", "14D", "--oos-span", "14D",
        "--step", "14D", "--max-total-runs", "100",
    ]


#: 3 CLI の（パーサ生成関数, 最小 argv 生成関数）。実行入口すべてを同じ規律で見る。
_CLIS = {
    "run_is_oos_cli": (run_is_oos_cli._build_arg_parser, _run_is_oos_argv),
    "optimize_cli": (optimize_cli._build_arg_parser, _optimize_argv),
    "walk_forward_cli": (walk_forward_cli._build_arg_parser, _walk_forward_argv),
}


@pytest.fixture(scope="module")
def spec() -> dict:
    """供給元スナップショットの銘柄仕様 8 項目（唯一の権威）。"""
    return load_spec_fields(OANDA_JAPAN_MT5_LIVE, _SYMBOL)


def _parse(name: str, extra: "list[str] | None" = None):
    parser, argv = _CLIS[name]
    return parser().parse_args(argv() + list(extra or []))


# --- 1. パーサが銘柄仕様の既定値を持たない -------------------------------------------


@pytest.mark.parametrize("name", sorted(_CLIS))
def test_cli_parsers_hold_no_symbol_spec_defaults(name):
    parser = _CLIS[name][0]()
    defaults = {key: parser.get_default(key) for key in SPEC_KEYS}
    assert defaults == {key: None for key in SPEC_KEYS}


@pytest.mark.parametrize("name", sorted(_CLIS))
def test_cli_parsers_still_declare_every_symbol_spec_option(name):
    """**負の対照**: 「既定値なし」は引数を消して達成したのではない（8 項目とも受け付ける）。"""
    parser = _CLIS[name][0]()
    options = {opt for action in parser._actions for opt in action.option_strings}
    assert {spec_option(key) for key in SPEC_KEYS} <= options


# --- 2. 既定 argv の解決結果が供給元と一致 -------------------------------------------


@pytest.mark.parametrize("name", sorted(_CLIS))
def test_cli_resolves_the_symbol_spec_from_the_snapshot(name, spec):
    assert resolve_symbol_spec(_parse(name)) == spec


def test_the_default_symbol_is_registered_at_the_supplier():
    """上の検定が成立する前提（既定 ``--symbol`` に供給元スナップショットが実在する）。"""
    for name in _CLIS:
        symbol = _CLIS[name][0]().get_default("symbol")
        assert snapshot_path(OANDA_JAPAN_MT5_LIVE, symbol).exists()


def test_snapshot_disagrees_with_the_removed_defaults(spec):
    """**負の対照**: 撤去した既定値は供給元と一致しない（上の一致検定は空虚でない）。"""
    disagreeing = {
        key: value for key, value in _REMOVED_DEFAULTS.items() if spec[key] != value
    }
    assert disagreeing == _REMOVED_DEFAULTS


# --- 3. 明示指定は優先される（ただし食い違えば警告） ----------------------------------


def test_explicit_value_overrides_the_snapshot(spec, capsys):
    args = _parse("run_is_oos_cli", ["--contract-size", "3.0"])
    resolved = resolve_symbol_spec(args)
    assert resolved["contract_size"] == 3.0
    assert {k: v for k, v in resolved.items() if k != "contract_size"} == {
        k: v for k, v in spec.items() if k != "contract_size"
    }


def test_explicit_value_disagreeing_with_the_snapshot_is_warned(spec, capsys):
    resolve_symbol_spec(_parse("run_is_oos_cli", ["--contract-size", "3.0"]))
    err = capsys.readouterr().err
    assert "contract_size" in err
    assert "3.0" in err
    assert str(spec["contract_size"]) in err


def test_explicit_value_agreeing_with_the_snapshot_is_not_warned(spec, capsys):
    """**負の対照**: 一致する明示指定では警告が出ない（警告が常時出るのではない）。"""
    resolve_symbol_spec(
        _parse("run_is_oos_cli", ["--contract-size", str(spec["contract_size"])])
    )
    assert capsys.readouterr().err == ""


# --- 4. 未登録銘柄 + 明示指定なし → fail-loud -----------------------------------------


def test_unregistered_symbol_without_explicit_values_fails_loud():
    args = _parse("run_is_oos_cli", ["--symbol", _UNREGISTERED])
    with pytest.raises(SymbolSpecArgsError) as ei:
        resolve_symbol_spec(args)
    message = str(ei.value)
    # 「どの銘柄か」「どう取得するか」「どう明示指定するか」の 3 点が読み取れること。
    assert _UNREGISTERED in message
    assert "tools/capture_mt5_symbol_spec.py" in message
    for key in SPEC_KEYS:
        assert spec_option(key) in message


def test_the_unregistered_symbol_really_has_no_snapshot():
    """**負の対照**: fail-loud 検定の入力が本当に未登録である（空虚でない）。"""
    assert not snapshot_path(OANDA_JAPAN_MT5_LIVE, _UNREGISTERED).exists()


# --- 5. 8 項目すべて明示なら供給元不在でも通る ----------------------------------------


def test_unregistered_symbol_with_every_value_explicit_is_accepted(spec, capsys):
    explicit: "list[str]" = ["--symbol", _UNREGISTERED]
    for key in SPEC_KEYS:
        explicit += [spec_option(key), str(spec[key])]
    resolved = resolve_symbol_spec(_parse("run_is_oos_cli", explicit))
    assert resolved == {key: SPEC_FIELD_SOURCES[key].cast(spec[key]) for key in SPEC_KEYS}
    # 供給元が引けない以上、食い違いの警告は出しようがない（無言で既定値を使うのとは別物）。
    assert capsys.readouterr().err == ""


def test_unregistered_symbol_with_one_value_missing_still_fails_loud(spec):
    """**負の対照**: 1 項目でも欠けたら通らない（「明示指定あり」の判定が緩くない）。"""
    keys = list(SPEC_KEYS)
    explicit: "list[str]" = ["--symbol", _UNREGISTERED]
    for key in keys[:-1]:
        explicit += [spec_option(key), str(spec[key])]
    with pytest.raises(SymbolSpecArgsError) as ei:
        resolve_symbol_spec(_parse("run_is_oos_cli", explicit))
    assert spec_option(keys[-1]) in str(ei.value)


# --- 6. EA 入力 lot も既定値を持たず供給元の最小発注単位から来る ------------------------


def test_cli_parsers_hold_no_lot_size_default():
    for name in _CLIS:
        assert _CLIS[name][0]().get_default(_LOT) is None, name


def test_cli_parsers_still_declare_the_lot_size_option():
    """**負の対照**: 「既定値なし」は引数を消して達成したのではない（明示指定は可能）。"""
    for name in _CLIS:
        options = {opt for action in _CLIS[name][0]()._actions for opt in action.option_strings}
        assert spec_option(_LOT) in options, name


@pytest.mark.parametrize("name", sorted(_CLIS))
def test_cli_resolves_the_lot_size_from_the_supplied_minimum_volume(name, spec):
    """既定 lot は供給元の最小発注単位（``volume_min``）。期待値はテスト側に書かない。"""
    args = _parse(name)
    assert resolve_lot_size(args, resolve_symbol_spec(args)) == spec["volume_min"]


def test_the_removed_lot_default_disagrees_with_the_supplied_minimum_volume(spec):
    """**負の対照**: 撤去した既定 lot は供給元の最小発注単位と一致しない（上は空虚でない）。"""
    assert _REMOVED_LOT_DEFAULT != spec["volume_min"]


def test_explicit_lot_size_overrides_the_supplied_minimum_volume(spec):
    """明示指定は 8 項目と**対称**に優先する（呼出時の意図であり、コマンド行に見える）。"""
    args = _parse("run_is_oos_cli", [spec_option(_LOT), str(_other_lot(spec))])
    assert resolve_lot_size(args, resolve_symbol_spec(args)) == _other_lot(spec)


def test_the_explicit_lot_used_above_differs_from_the_supplied_minimum_volume(spec):
    """**負の対照**: 上の明示値は既定と別物（「明示優先」が既定と見分けられている）。"""
    assert _other_lot(spec) != spec["volume_min"]


def test_explicit_lot_size_is_not_warned_when_it_differs_from_the_minimum_volume(
    spec, capsys
):
    """lot の食い違いは**警告しない**（8 項目との意図的な非対称・module docstring）。

    供給元は ``lot_size`` を持たず ``volume_min`` は下限であって lot の供給値ではない。
    下限より大きい lot は正当な指定であり、警告にすると正当な使い方のたびに鳴る。
    """
    args = _parse("run_is_oos_cli", [spec_option(_LOT), str(_other_lot(spec))])
    resolve_lot_size(args, resolve_symbol_spec(args))
    assert capsys.readouterr().err == ""


def test_a_disagreeing_symbol_spec_item_is_still_warned_on_the_same_path(spec, capsys):
    """**負の対照**: 同じ呼び出し列で銘柄仕様が食い違えば警告は出る（無警告は lot 限定）。"""
    args = _parse(
        "run_is_oos_cli",
        [spec_option(_LOT), str(_other_lot(spec)), "--contract-size", "3.0"],
    )
    resolve_lot_size(args, resolve_symbol_spec(args))
    assert "contract_size" in capsys.readouterr().err


def test_unregistered_symbol_fails_loud_even_when_the_lot_is_explicit(spec):
    """未登録銘柄の扱いは 8 項目と**同じ** fail-loud（lot の明示は仕様の欠落を救わない）。"""
    args = _parse(
        "run_is_oos_cli",
        ["--symbol", _UNREGISTERED, spec_option(_LOT), str(_other_lot(spec))],
    )
    with pytest.raises(SymbolSpecArgsError):
        resolve_lot_size(args, resolve_symbol_spec(args))


def test_unregistered_symbol_with_every_value_explicit_resolves_the_lot_too(spec):
    """**負の対照**: 8 項目を明示すれば未登録銘柄でも lot まで解決する（無条件に落ちない）。

    既定 lot は**解決済み仕様**から引くのであって供給元スナップショットを直接引かない
    （＝仕様が解決できた経路では必ず lot も解決できる）ことの実証でもある。
    """
    explicit: "list[str]" = ["--symbol", _UNREGISTERED]
    for key in SPEC_KEYS:
        explicit += [spec_option(key), str(spec[key])]
    args = _parse("run_is_oos_cli", explicit)
    assert resolve_lot_size(args, resolve_symbol_spec(args)) == spec["volume_min"]
