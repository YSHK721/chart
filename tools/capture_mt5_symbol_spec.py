#!/usr/bin/env python3
"""MT5 端末から銘柄仕様のスナップショットを取得する（Windows VM 上で実行・読み取りのみ）。

設計: ``.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md`` §3.2 / §3.4 / §5（ISSUE-445 恒久策・段階 2 の前提物）。

## 何を解くのか

ISSUE-445 の根本原因 RC-1 は「値が 1 つ間違っていたこと」ではなく、
**供給元（MT5 端末）が出力していない値を、人が権威台帳へ書き足せる構造**そのものである。
実際 ``case.yaml`` の ``contract_size: 10`` は MT5 レポートに一度も現れない逆算値であり、
2 か月以上検出されなかった。

したがって本スクリプトの不変条件は「**人が値を選ばない・書かない**」ことである:

- ``symbol`` セクションは ``mt5.symbol_info(symbol)._asdict()`` を **丸ごと** 落とす。
  フィールドの取捨選択をしない。MT5 がフィールドを増やせばそのまま増える。
- ``account`` セクションだけは規則で絞る（後述）。ここが唯一の「規則をコードに書く」箇所である。
- ``marketdata/symbol_specs/<server>/<symbol>.json`` は機械生成物であり、手で編集しない
  （既存 ``indigators/indicator_ui/web/js/domain/symbol_spec_generated.js`` と同じ規律）。
  ISSUE-445 の実測表を人が JSON へ書き写す行為は **RC-1 の再生産**であり、行わない。

## なぜ account だけ許可リストで絞るのか（規則と理由）

含める: ``leverage`` / ``currency`` / ``trade_mode`` / ``company`` / ``server``
除外する: 口座の識別子（``login`` 等）と変動値（``balance`` / ``equity`` / ``margin`` /
``margin_free`` / ``margin_level`` / ``profit`` / ``credit`` / ``assets`` / ``liabilities`` 等）

理由は 2 つ:

1. **機微情報を台帳に置かない。** スナップショットはリポジトリへコミットされる想定である。
   口座番号・残高が版管理に入ると取り消せない。
2. **変動値は仕様ではない。** 残高・評価損益は取得のたびに変わる。台帳に入れると再取得の差分が
   全面的にノイズ化し、「仕様が変わった」という本来検出したい信号が埋もれる。

実装は **含める側の列挙（許可リスト）** で行う。除外側の列挙にすると、将来 MT5 が
フィールドを増やしたときに機微値が黙って混入する側へ倒れる。許可リストなら、増えた
フィールドは黙って落ちる（安全側）。``leverage`` を account 側から供給するのは、
``mt5.symbol_info()`` に ``leverage`` が存在しない（ISSUE-445 実測）ためである（設計書 §3.4）。

## 安全性（接続先は実弾のライブ口座である）

使う API は読み取り系だけ: ``initialize`` / ``symbol_select`` / ``symbol_info`` /
``account_info`` / ``terminal_info`` / ``history_deals_get`` / ``last_error`` / ``shutdown``。
発注系（``order_*``）は 1 つも呼ばない。この禁止は宣言ではなく
``tools/tests/test_capture_mt5_symbol_spec.py`` の AST 走査が施行する。

``--with-deals`` の出力先は **リポジトリ配下を禁止** する（残高・約定情報のコミット事故の防止）。

## 依存注入（DIP）

``MetaTrader5`` をトップレベル import しない。取得関数は mt5 モジュール相当のオブジェクトを
引数で受け取る。これにより MetaTrader5 が存在しない Linux コンテナでも import と ``--help`` が
通り、fake を注入した単体検定が成立する。

## 実行（Windows VM・MT5 端末が起動している状態で）

    python tools\\capture_mt5_symbol_spec.py --symbol JP225

出力: ``marketdata/symbol_specs/<server>/<symbol>.json``（``--out`` で上書き可）。

## 未検証（コンテナに MetaTrader5 が無いため実機確認していない）

- ``symbol_select()`` の戻り値が bool であること。本実装は falsy を Fail-Stop として扱う。
- ``history_deals_get()`` に渡す datetime の時刻系。本実装は UTC の naive datetime を渡す。
- ``MetaTrader5.__version__`` の存在と型。無ければ ``null``、str でなければ ``str()`` を記録する。
"""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

#: リポジトリ根。約定履歴の出力先がここの配下なら中断する（コミット事故の防止）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 生成物の相対配置（設計書 §3.2）。
SNAPSHOT_DIR = "marketdata/symbol_specs"

#: 本スクリプトの識別子。生成物の ``_generated`` に埋め、出所を辿れるようにする。
GENERATOR = "tools/capture_mt5_symbol_spec.py"

#: ``account`` セクションに含めるキー（許可リスト。理由は module docstring）。
ACCOUNT_KEYS: "tuple[str, ...]" = ("company", "currency", "leverage", "server", "trade_mode")

#: ``meta.terminal`` に記録する ``terminal_info()`` のキー。
TERMINAL_KEYS: "tuple[str, ...]" = ("company", "name", "build")

#: パス成分に許すのは ASCII 英数と ``.`` ``_`` ``-`` のみ。それ以外は 1 文字 1 文字を
#: ``-`` へ機械的に置換する（1 文字 → 1 文字。長さは変えない）。
#: 例: ``OANDA-Japan MT5 Live`` → ``OANDA-Japan-MT5-Live`` / ``a/b\\c`` → ``a-b-c``。
#: 置換後が空白のみ・``.``・``..`` になる場合は、親ディレクトリへ逃げる経路になるため中断する。
_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_REPLACEMENT = "-"


class CaptureError(RuntimeError):
    """取得の前提が崩れたことを表す（Fail-Stop）。黙って空ファイルを書かない。"""


# ---------------------------------------------------------------------
# 純粋関数（MT5 を知らない）
# ---------------------------------------------------------------------

def sanitize_path_component(raw: str) -> str:
    """サーバ名・銘柄名をファイルパス成分へ機械的に変換する（規則は ``_SAFE_CHARS`` を参照）。"""
    text = "" if raw is None else str(raw)
    converted = "".join(c if c in _SAFE_CHARS else _REPLACEMENT for c in text)
    if not text.strip() or converted in (".", ".."):
        raise CaptureError(
            f"パス成分として使えない値です: {raw!r} → {converted!r}。"
            " 供給元の server / symbol を確認してください。"
        )
    return converted


def default_out_path(server: str, symbol: str) -> Path:
    """既定の出力先を返す（ディレクトリは作らない）。"""
    return REPO_ROOT / SNAPSHOT_DIR / sanitize_path_component(server) / (
        f"{sanitize_path_component(symbol)}.json"
    )


def generated_marker() -> "dict[str, str]":
    """「自動生成・手で編集しない」を生成物自身に書く（JSON はコメントを持てないため）。"""
    return {
        "note": "自動生成物。手で編集しない。値を直したくなったら供給元（MT5 端末）で再取得する。",
        "generator": GENERATOR,
        "authority": "MT5 端末（mt5.symbol_info / mt5.account_info）が唯一の権威。",
        "captured_at_utc_meaning": (
            "meta.captured_at_utc はスナップショットを取得した実時刻（UTC）であり、"
            "相場時刻ではない。"
        ),
    }


def account_section(account: Mapping[str, Any]) -> "dict[str, Any]":
    """許可リストのキーだけを通す。無いキーは捏造せず省略する。"""
    return {k: account[k] for k in ACCOUNT_KEYS if k in account}


def terminal_section(terminal: "Mapping[str, Any] | None") -> "dict[str, Any] | None":
    """``terminal_info()`` から 3 キーを取る。取得できなければ ``None``（捏造しない）。"""
    if terminal is None:
        return None
    return {k: terminal.get(k) for k in TERMINAL_KEYS}


def build_snapshot(
    *,
    symbol: str,
    symbol_info: Mapping[str, Any],
    account_info: Mapping[str, Any],
    terminal_info: "Mapping[str, Any] | None",
    captured_at: datetime,
    mt5_package_version: "str | None",
) -> "dict[str, Any]":
    """スナップショット本体を組む。``symbol`` は丸ごと・``account`` だけ許可リストで絞る。"""
    return {
        "_generated": generated_marker(),
        "meta": {
            "captured_at_utc": _iso_utc(captured_at),
            "symbol": symbol,
            "terminal": terminal_section(terminal_info),
            "mt5_package_version": mt5_package_version,
        },
        "symbol": dict(symbol_info),
        "account": account_section(account_info),
    }


def serialize(payload: Mapping[str, Any]) -> str:
    """決定的に直列化する（同じ入力 → 同じバイト列。再取得の差分をノイズにしない）。"""
    try:
        return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    except TypeError as exc:  # 供給元が JSON にできない値を返した
        raise CaptureError(f"JSON へ直列化できない値が含まれています: {exc}") from exc


def write_text_lf(path: Path, text: str) -> None:
    """LF 改行・UTF-8 で書く（Windows で実行しても CRLF にしない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(text)


def resolve_deals_out(raw: str) -> Path:
    """約定履歴の出力先を解決する。リポジトリ配下なら中断する。

    約定履歴には残高・損益が含まれる。リポジトリ配下に書けるようにすると、いずれ
    ``git add`` で取り込まれる。**経路の側で不可能にする**（運用の注意では防げない）。
    """
    path = Path(raw).expanduser().resolve()
    root = REPO_ROOT.resolve()
    if path == root or root in path.parents:
        raise CaptureError(
            f"約定履歴の出力先がリポジトリ配下です: {path}。"
            " 残高・約定情報がコミットされる事故を防ぐため、リポジトリ外を指定してください"
            f"（リポジトリ根: {root}）。"
        )
    return path


def _iso_utc(moment: datetime) -> str:
    """UTC の ISO8601（``...Z``）へ。tz 無しは UTC とみなす。"""
    if moment.tzinfo is None:
        aware = moment.replace(tzinfo=timezone.utc)
    else:
        aware = moment.astimezone(timezone.utc)
    return aware.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


# ---------------------------------------------------------------------
# MT5 との境界（注入された mt5 モジュール相当だけを触る）
# ---------------------------------------------------------------------

def _default_mt5():
    """既定の供給元。トップレベル import しないのは DIP のため（module docstring 参照）。"""
    import MetaTrader5  # noqa: PLC0415  (遅延 import は意図的)

    return MetaTrader5


def _last_error(mt5: Any) -> str:
    try:
        return f"last_error={mt5.last_error()!r}"
    except Exception as exc:  # 供給元が last_error を持たない場合も原因を落とさない
        return f"last_error=<取得できません: {exc!r}>"


def _as_dict(record: Any) -> "dict[str, Any]":
    """``mt5`` が返す namedtuple を dict へ（フィールドを選ばない）。"""
    return dict(record._asdict())


def _package_version(mt5: Any) -> "str | None":
    version = getattr(mt5, "__version__", None)
    if version is None:
        return None
    return version if isinstance(version, str) else str(version)


@contextmanager
def mt5_session(mt5: Any):
    """端末との接続を **1 回だけ** 開いて閉じる（Composition Root が所有する）。

    ``shutdown()`` 後に ``initialize()`` を再度呼べるかは**未検証**である。仮定を持ち込まない
    ため、1 実行あたりの接続は 1 回に限る（``--with-deals`` でもセッションは 1 つ）。
    """
    if not mt5.initialize():
        raise CaptureError(f"mt5.initialize() が失敗しました（{_last_error(mt5)}）")
    try:
        yield mt5
    finally:
        mt5.shutdown()


def read_snapshot(mt5: Any, symbol: str, *, captured_at: datetime) -> "dict[str, Any]":
    """開いているセッションから銘柄仕様を読む（読み取りのみ）。"""
    if not mt5.symbol_select(symbol, True):
        raise CaptureError(
            f"mt5.symbol_select({symbol!r}) が失敗しました（{_last_error(mt5)}）。"
            " 銘柄名を確認してください。"
        )
    info = mt5.symbol_info(symbol)
    if info is None:
        raise CaptureError(
            f"mt5.symbol_info({symbol!r}) が None を返しました（{_last_error(mt5)}）"
        )
    account = mt5.account_info()
    if account is None:
        raise CaptureError(f"mt5.account_info() が None を返しました（{_last_error(mt5)}）")
    terminal = mt5.terminal_info()
    return build_snapshot(
        symbol=symbol,
        symbol_info=_as_dict(info),
        account_info=_as_dict(account),
        terminal_info=None if terminal is None else _as_dict(terminal),
        captured_at=captured_at,
        mt5_package_version=_package_version(mt5),
    )


def read_deals(mt5: Any, *, captured_at: datetime, days: int) -> "dict[str, Any]":
    """開いているセッションから実約定を読む（ISSUE-445「次の一手 2」のライブ側追認用）。

    スナップショットとは **別ファイル** に出す。残高・損益を仕様の台帳へ混ぜない。
    ``history_deals_get`` に渡す datetime の時刻系は未検証のため、UTC の naive datetime を渡す。
    """
    date_to = captured_at.astimezone(timezone.utc).replace(tzinfo=None)
    date_from = date_to - timedelta(days=days)
    deals = mt5.history_deals_get(date_from, date_to)
    if deals is None:
        raise CaptureError(
            f"mt5.history_deals_get() が None を返しました（{_last_error(mt5)}）"
        )
    return {
        "_generated": generated_marker(),
        "meta": {
            "captured_at_utc": _iso_utc(captured_at),
            "from_utc": _iso_utc(date_from),
            "to_utc": _iso_utc(date_to),
            "mt5_package_version": _package_version(mt5),
        },
        "deals": [_as_dict(d) for d in deals],
    }


# ---------------------------------------------------------------------
# CLI（Composition Root）
# ---------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=GENERATOR,
        description=(
            "MT5 端末から銘柄仕様のスナップショットを取得する（読み取りのみ・発注系 API は呼ばない）。"
        ),
        epilog=(
            "既定の出力先: %s/<server>/<symbol>.json（機械生成物・手で編集しない）。"
            % SNAPSHOT_DIR
        ),
    )
    parser.add_argument("--symbol", required=True, help="取得する銘柄名（例: JP225）")
    parser.add_argument("--out", default=None, help="出力先 JSON（既定は server / symbol から決まる）")
    parser.add_argument(
        "--with-deals",
        action="store_true",
        help="実約定も取得する（--deals-out 必須・リポジトリ配下は不可）",
    )
    parser.add_argument("--deals-out", default=None, help="約定履歴の出力先（リポジトリ外）")
    parser.add_argument(
        "--deals-days", type=int, default=30, help="約定履歴の遡り日数（既定 30）"
    )
    return parser


def main(
    argv: "Sequence[str] | None" = None,
    *,
    mt5: Any = None,
    now: "datetime | None" = None,
) -> int:
    args = build_parser().parse_args(argv)
    captured_at = now or datetime.now(timezone.utc)
    try:
        # 端末へ触る前に経路を検査する（接続してから落ちると副作用の切り分けが濁る）。
        deals_out = None
        if args.with_deals:
            if not args.deals_out:
                raise CaptureError("--with-deals には --deals-out（リポジトリ外）が必要です")
            deals_out = resolve_deals_out(args.deals_out)

        supplier = mt5 if mt5 is not None else _default_mt5()
        # 接続は 1 回だけ（再 initialize の可否は未検証 → 仮定を持ち込まない）。
        with mt5_session(supplier) as terminal:
            snapshot = read_snapshot(terminal, args.symbol, captured_at=captured_at)
            deals = (
                None if deals_out is None
                else read_deals(terminal, captured_at=captured_at, days=args.deals_days)
            )

        # 書き出しは接続を閉じてから（端末を掴んだまま I/O で待たない）。
        out = Path(args.out) if args.out else default_out_path(
            snapshot["account"].get("server", ""), args.symbol
        )
        write_text_lf(out, serialize(snapshot))
        print(f"snapshot: {out}")

        if deals_out is not None:
            write_text_lf(deals_out, serialize(deals))
            print(f"deals   : {deals_out}（リポジトリ外・コミットしない）")
        return 0
    except CaptureError as exc:
        print(f"[FAIL-STOP] {exc}", file=sys.stderr)
        return 2
    except ModuleNotFoundError as exc:
        print(
            f"[FAIL-STOP] MetaTrader5 パッケージが見つかりません: {exc}。"
            " 本スクリプトは MT5 端末が動く Windows 上で実行してください。",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI エントリ
    sys.exit(main())
