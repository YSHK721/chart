"""symbol_spec_args — 実行入口 CLI の銘柄仕様引数を宣言し、解決する単一ソース。

由来: ISSUE-445 恒久策の**本番残渣（最後の 1 件）**／``.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md`` §3.2。

## 何を解くのか（RC-1）

ISSUE-445 の根本原因は「値が 1 つ間違っていたこと」ではなく、**供給元が出力していない値を
人が権威台帳へ書き足せる構造**である。``simulator/tools/`` の 3 CLI
（``run_is_oos_cli`` / ``optimize_cli`` / ``walk_forward_cli``）は銘柄仕様 8 項目すべてを
argparse の**既定値**として持っていた（``--contract-size 10.0`` / ``--volume-min 0.01`` /
``--volume-max 100.0`` / ``--volume-step 0.01`` / ``--stops-level 0`` / ``--digits 1`` /
``--point-size 0.1`` / ``--leverage 10.0``）。既定値は台帳と同じであり、しかもコマンド行に
現れないぶん台帳より見えない。既定のまま実行すると損益が約 10 倍ずれた結果が**無言で**出る。

本モジュールは 3 CLI から「銘柄仕様引数の宣言と解決」だけを取り出したものである。
同じ解決を 3 ファイルへ手書きで複製すると必ず取り残しが出る（本件がまさにその実例——
``b440a9d`` で本番ツール 2 件を是正した時点で、同じ誤りが 3 CLI に残っていた）。

## 3 つの規律

1. **既定値を置かない**。argparse は ``default=None``（未指定＝「人が値を選ばなかった」）。
2. **未指定は供給元から引く**。権威は ``marketdata.symbol_spec_snapshot``（MT5 端末から
   機械取得したスナップショット）だけであり、ここに数値を 1 つも書かない。
3. **引けず、明示もされていなければ中断する**（:class:`SymbolSpecArgsError`）。黙って推定値を
   使わない。「動かすために既定値を残す」は症状の出る条件を避けただけで RC-1 を消していない。

明示指定は供給元に**優先する**。コマンドラインの明示は「人が書いた台帳」ではなく呼出時の意図で
あり、かつコマンド行に見えるからである（what-if 実行＝探索 CLI の存在理由でもある）。ただし
供給元と食い違えば **stderr へ警告する**。ISSUE-445 の失敗モードは「誤った値が 2 か月以上
誰にも気付かれなかったこと」であり、貼り付け回された古いコマンドが同じ 10 倍差を再生産しても
無言のままになる経路をふさぐ。中断ではなく警告にするのは、明示値は供給元の写しではなく
**意図的な仮定**でありうるためである（供給元と一致していなければ即誤りとは言えない）。

## 供給元（サーバ）名を CLI 引数にしない理由

サーバ名は「銘柄仕様の値」ではなく「どの台帳を引くか」の指定である。実測（2026-08-26）で
``marketdata/symbol_specs/`` に存在する供給元は ``OANDA-Japan-MT5-Live`` の **1 つだけ**であり、
唯一の正当値しか取り得ない引数は情報を持たない（YAGNI）。また既に是正済みの本番ツール
（``export_trade_markers`` / ``export_report_payload``）と ``SymbolSpecCatalog`` はいずれも
モジュール定数として供給元を束ねており、CLI だけ別の作法にすると同じ概念に 2 つの呼び名ができる。
供給元が増えたときは :data:`SPEC_SERVER` の与え方（引数化）を 1 か所で決めればよい。
なお **``--symbol`` は利用者が上書きできる**（銘柄の同一性の指定であって値ではない）。
そのため未登録銘柄が入りうる＝上の規律 3（fail-loud）が要る。

## 依存方向

``marketdata``（依存ゼロの最下層）→ 本モジュール（tools 層）の一方向のみ。逆流させない。
``simulator.main`` も ``argparse`` の実行も知らない（引数の宣言と解決だけを持つ・SRP）。
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Mapping

from marketdata.symbol_spec_snapshot import (
    OANDA_JAPAN_MT5_LIVE,
    SPEC_FIELD_SOURCES,
    SnapshotError,
    load_spec_fields,
)

#: 銘柄仕様 8 項目の名前。**列挙をここに書き写さない**（対応表の所有者は供給元ローダ）。
SPEC_KEYS: "tuple[str, ...]" = tuple(SPEC_FIELD_SOURCES)

#: 供給元（どの台帳を引くか）。値ではないためモジュール定数で束ねる（module docstring）。
SPEC_SERVER = OANDA_JAPAN_MT5_LIVE

#: 取得手順の案内（``SnapshotError`` が案内するものと同じ実体を指す）。
_CAPTURE_TOOL = "tools/capture_mt5_symbol_spec.py"


class SymbolSpecArgsError(RuntimeError):
    """銘柄仕様を解決できなかったことを表す（Fail-Stop）。既定値で埋めない。

    ``run_is_oos_cli.OutputGuardError`` と同じ作法で、握り潰さず main の外へ抜ける。
    """


def spec_option(name: str) -> str:
    """フィールド名 → CLI オプション名（``contract_size`` → ``--contract-size``）。

    綴りの写し取りを作らないため、オプション名は**フィールド名から導出**する。
    """
    return "--" + name.replace("_", "-")


def add_symbol_spec_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """銘柄仕様 8 項目を ``default=None`` で宣言する（既定値を置かない）。

    型は供給元の対応表（``FieldSource.cast``）から取る。argparse 側に型表を書き写すと、
    供給元が型を変えたとき片方だけが腐る。
    """
    for name, source in SPEC_FIELD_SOURCES.items():
        parser.add_argument(
            spec_option(name),
            type=source.cast,
            default=None,
            help=(
                f"銘柄仕様 {name}（既定値なし。未指定なら供給元スナップショット"
                f" {SPEC_SERVER}/<symbol>.json から引く）"
            ),
        )
    return parser


def _warn_on_disagreement(
    explicit: "Mapping[str, Any]", supplied: "Mapping[str, Any]", symbol: str
) -> None:
    """明示値が供給元と食い違えば stderr へ告げる（無言にしない・module docstring）。"""
    for name, value in explicit.items():
        if supplied[name] != value:
            sys.stderr.write(
                f"[warn] {spec_option(name)}={value} は供給元"
                f"（{SPEC_SERVER}/{symbol}）の {name}={supplied[name]} と食い違います"
                "（明示指定を優先します）。\n"
            )


def _unresolved_error(symbol: str, missing: "list[str]", cause: SnapshotError) -> SymbolSpecArgsError:
    """「どうすれば直せるか」が読み取れる中断メッセージを組む（SnapshotError の作法に揃える）。"""
    options = " ".join(spec_option(name) for name in missing)
    return SymbolSpecArgsError(
        f"銘柄仕様を解決できません（symbol={symbol!r} / server={SPEC_SERVER!r}）。"
        f" 未指定の {len(missing)} 項目: {options}。"
        f" 供給元からの読み込みに失敗しました: {cause}"
        f" 次のいずれかで解決してください:"
        f" (1) {_CAPTURE_TOOL} を MT5 端末上で実行して当該銘柄のスナップショットを取得する。"
        f" (2) 上記の項目をコマンドラインで明示指定する。"
        " 既定値は置きません（人が書いた値が権威になる形＝ISSUE-445 RC-1 を再生させないため）。"
    )


def resolve_symbol_spec(args: argparse.Namespace) -> "dict[str, Any]":
    """``args`` から銘柄仕様 8 項目を解決して返す（``build_interactor`` へ ``**`` 展開する形）。

    決め方:
        * 明示指定された項目はその値を使う（供給元と食い違えば stderr へ警告）。
        * 明示されていない項目は ``SPEC_SERVER`` / ``args.symbol`` のスナップショットから引く。
        * 引けず、かつ明示もされていない項目が 1 つでもあれば :class:`SymbolSpecArgsError`。

    8 項目すべてが明示指定なら供給元を読まなくても解決できる（未登録銘柄でも実行できる）。
    その場合は突き合わせる相手が存在しないため警告も出ない——**既定値を無言で使うのとは別物**で、
    値はすべてコマンド行に現れている。
    """
    explicit = {
        name: getattr(args, name)
        for name in SPEC_KEYS
        if getattr(args, name) is not None
    }
    missing = [name for name in SPEC_KEYS if name not in explicit]
    symbol = args.symbol
    try:
        supplied = load_spec_fields(SPEC_SERVER, symbol)
    except SnapshotError as exc:
        if missing:
            raise _unresolved_error(symbol, missing, exc) from exc
        return dict(explicit)  # 全項目が明示＝供給元を必要としない
    _warn_on_disagreement(explicit, supplied, symbol)
    return {**supplied, **explicit}


__all__ = [
    "SPEC_KEYS",
    "SPEC_SERVER",
    "SymbolSpecArgsError",
    "spec_option",
    "add_symbol_spec_arguments",
    "resolve_symbol_spec",
]
