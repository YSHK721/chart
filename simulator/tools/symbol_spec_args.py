"""symbol_spec_args — 実行入口 CLI の「供給元から来る引数」を宣言し、解決する単一ソース。

対象は銘柄仕様 8 項目と、既定値を供給元から引く EA 入力 ``lot_size``（下記「lot_size の扱い」）。

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

## lot_size の扱い（2026-08-26・依頼者裁定）

``lot_size`` は銘柄仕様ではなく **EA 入力**だが、既定値 ``0.1`` は同じ「人が書いた数」であった。
供給元の ``volume_min=1.0`` の下では、原典 ``.mq5`` を持たず ``NormalizeLot`` 相当を実装しない
素通し戦略（``TC24051901``）が発注できない（実測 2026-08-26: ``InvalidPriceError``）。
既定を供給元の**最小発注単位**（``volume_min``）にすると、原典 EA の ``NormalizeLot(0.1)`` の
戻り値と同値になり、どちらの戦略でも同じ実効 lot になる。前例は ``export_trade_markers``
（``b440a9d`` で ``lot_size=spec["volume_min"]`` を採用）であり、同じ作法に揃える。
規律 1〜3 のうち 1（既定値を置かない）と 2（未指定は供給元から引く）は lot にも同じく適用する。
3（fail-loud）は銘柄仕様の解決が先に走るため**自動的に同じ**になる（:func:`resolve_lot_size`）。

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


def add_lot_size_argument(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """EA 入力 ``--lot-size`` を ``default=None`` で宣言する（既定値を置かない）。

    オプション名は :func:`spec_option` で導出する（綴りを写さない）。``lot_size`` は銘柄仕様
    ではないため :func:`add_symbol_spec_arguments` とは別の関数だが、**既定値を置かない**という
    規律は同じである（下の :func:`resolve_lot_size` を参照）。
    """
    parser.add_argument(
        spec_option("lot_size"),
        type=float,
        default=None,
        help="EA 入力 lot（既定値なし。未指定なら供給元の最小発注単位 volume_min を使う）",
    )
    return parser


def resolve_lot_size(args: argparse.Namespace, spec: "Mapping[str, Any]") -> float:
    """EA 入力 lot を解決する。明示指定が優先、未指定なら ``spec`` の最小発注単位。

    引数 ``spec`` は :func:`resolve_symbol_spec` の戻り値（＝**解決済み**の銘柄仕様）である。
    供給元スナップショットを直接引き直さないのは 2 つの理由による: (1) 解決済み仕様と食い違う
    lot を作らない（8 項目を明示して未登録銘柄を走らせる経路でも、その明示 ``volume_min`` が
    lot の既定になる）。(2) 解決を 2 度呼ぶと食い違い警告が二重に出る。
    したがって lot 固有の失敗経路は無く、未登録銘柄の fail-loud は 8 項目と**同じ**
    :class:`SymbolSpecArgsError`（呼出側が先に :func:`resolve_symbol_spec` を通るため）である。

    なぜ既定が ``volume_min`` か（``export_trade_markers`` の前例・``b440a9d``）:
        人が選んだ数ではなく**原典 EA の ``NormalizeLot(0.1)`` の戻り値と同値**だからである。
        原典 ``.mq5`` を移植した戦略は発注前に ``NormalizeLot`` を掛け、``volume_min`` 未満の
        lot を ``volume_min`` へ持ち上げる。素通し戦略（``TC24051901``・原典 ``.mq5`` を持たず
        正規化段が無い）ではその持ち上げが起きないため、Root が発注可能な lot を供給しなければ
        ``InvalidPriceError`` になる（実測 2026-08-26: 旧既定 0.1 は供給元 ``volume_min=1.0``
        の下で「volume が [volume_min, volume_max] 範囲外」）。既定を供給元由来にすると
        **どちらの戦略でも同じ実効 lot** になる。

    明示指定を供給元と突き合わせて警告しないのは 8 項目との**意図的な非対称**である。
    供給元は ``lot_size`` という値を持たない——``volume_min`` は lot の**下限**であって
    lot の供給値ではない。下限より大きい lot は正当な指定（例: 2 ロット）であり、食い違いを
    警告にすると正当な使い方のたびに鳴って誤りを識別できない。識別できる条件（下限割れ・
    刻み外れ）は :meth:`simulator.domain.order.Order.validate` が既に所有し
    ``InvalidPriceError`` で落とす（実測: ``--lot-size 1.5`` は「volume が volume_step の
    倍数でない」）。同じ規則を CLI 側へ書き写すと所有者が 2 つになる。
    """
    return spec["volume_min"] if args.lot_size is None else args.lot_size


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
    "add_lot_size_argument",
    "resolve_symbol_spec",
    "resolve_lot_size",
]
