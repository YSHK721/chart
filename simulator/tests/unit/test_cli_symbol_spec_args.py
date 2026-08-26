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
