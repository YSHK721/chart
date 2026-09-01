#!/usr/bin/env python3
"""tools/mt5_tick_feed.py — MT5 端末の生ティックを HTTP で配る VM 側の単体ファイル。

配布形態（重要）:
    本スクリプトは**リポジトリごと配布せず単体ファイルとして Windows VM へ持ち込む**
    （MT5 端末は VM 側にしかない）。よってリポジトリ内の他モジュールを import しない。
    ``tools/capture_mt5_symbol_spec.py`` と同じ規律である。

確定原則（変更不可）— **VM 側は定義を持たない**:
    tick 木のレイアウト・marketdata の列名・DST/UTC 変換・配信ディレクトリを本ファイルに
    置かない。端末から読んだ構造化配列を ``tobytes()`` のまま返し、意味づけは全部コンテナ側
    （marketdata/mt5_ticks/ 配下）が既存権威を import して行う。定義が 2 箇所にあると、
    片方だけ直った瞬間に静かにズレた値が台帳へ入る。
    この宣言は ``tools/tests/test_mt5_tick_feed.py`` が **AST/文字列走査で強制**する（A-1〜A-8）。

接続先は実弾のライブ口座である:
    発注系 API を 1 つも参照しない（A-2）。触ってよい端末 API は
    :data:`ALLOWED_TERMINAL_APIS` に列挙したものだけで、これも AST で施行する（A-3）。
    エンドポイントは ``/ticks`` と ``/health`` の 2 本のみ・全経路で認証必須・
    特定 IF bind のみ・秘密は環境変数のみ・ファイル配信機構は持たない。

``.npy`` を使わない理由: 形式の決定性が未検証（設計 §4）。検証していない前提を転送契約に
置かない。直列化は ``tobytes()`` と ``dtype.descr`` の 2 つだけで表す。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, NamedTuple, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse

#: 触ってよい端末 API（発注系はここに無い）。広げるときは設計 §4 と検定を同時に変える。
ALLOWED_TERMINAL_APIS = frozenset({
    "initialize", "shutdown", "last_error", "version", "terminal_info",
    "symbol_select", "symbol_info", "symbol_info_tick", "account_info",
    "copy_ticks_range", "copy_ticks_from", "COPY_TICKS_INFO",
})

#: 公開するエンドポイント（2 本のみ）。
ENDPOINTS = ("/health", "/ticks")

#: 認証スキームと鮮度窓。コンテナ側 `marketdata/mt5_ticks/wire.py` と同じ値でなければならず、
#: その一致は検定 N-6（署名往復）が固定する。
AUTH_SCHEME = "MT5B1"
MAX_TIMESTAMP_SKEW_SECONDS = 120

#: 秘密の唯一の供給元。引数にもファイルにも置かない。
SECRET_ENV = "MT5_BRIDGE_SECRET"
MIN_SECRET_LENGTH = 16

#: 既定の待ち受け（ISSUE-446 実測のコンテナ→VM 経路）。全 IF への bind は禁止する。
DEFAULT_BIND = "172.16.162.129"
DEFAULT_PORT = 8771
_FORBIDDEN_BINDS = frozenset({"0.0.0.0", "::", "*", ""})

#: 1 応答の上限。際限のない要求で端末を詰まらせない。
MAX_ROWS_LIMIT = 200_000


class FeedError(RuntimeError):
    """要求を満たせないことを表す。``status`` がそのまま HTTP 応答になる。"""

    def __init__(self, status: int, kind: str, detail: str = "", last_error: Any = None):
        super().__init__(f"{status} {kind}: {detail}")
        self.status = status
        self.kind = kind
        self.detail = detail
        self.last_error = last_error

    def payload(self) -> bytes:
        body: "Dict[str, Any]" = {"error": self.kind, "detail": self.detail}
        if self.last_error is not None:
            body["last_error"] = list(self.last_error)
        return json.dumps(body, ensure_ascii=False).encode("utf-8")


class Response(NamedTuple):
    status: int
    headers: "Dict[str, str]"
    body: bytes


# ---------------------------------------------------------------------
# 認証（正準文字列はコンテナ側 wire と同一・N-6 が一致を固定する）
# ---------------------------------------------------------------------

class NonceCache:
    """使用済み nonce を鮮度窓のあいだ覚えておく（再生攻撃を拒む）。

    窓の外に出た nonce は捨てる。捨てないと常駐のあいだ無限に育つ。
    """

    def __init__(self, ttl_seconds: int = MAX_TIMESTAMP_SKEW_SECONDS * 2):
        self._ttl = ttl_seconds
        self._seen: "Dict[str, int]" = {}

    def claim(self, nonce: str, *, now: int) -> bool:
        """未使用なら記録して ``True``。使用済みなら ``False``。"""
        for old, seen_at in list(self._seen.items()):
            if now - seen_at > self._ttl:
                del self._seen[old]
        if nonce in self._seen:
            return False
        self._seen[nonce] = now
        return True


def canonical_string(method: str, path: str, query: "Dict[str, str]", *, ts: int, nonce: str) -> str:
    """署名対象 ``METHOD\\n/path\\n<sorted-query>\\n<ts>\\n<nonce>``。"""
    sorted_query = urlencode(sorted((str(k), str(v)) for k, v in query.items()))
    return "\n".join([method.upper(), path, sorted_query, str(int(ts)), str(nonce)])


def _parse_authorization(header: str) -> "Dict[str, str]":
    text = (header or "").strip()
    prefix = AUTH_SCHEME + " "
    if not text.startswith(prefix):
        raise FeedError(401, "auth", "認証スキームが違います。")
    fields: "Dict[str, str]" = {}
    for part in text[len(prefix):].split(","):
        name, sep, value = part.partition("=")
        if not sep:
            raise FeedError(401, "auth", "認証ヘッダの形式が違います。")
        fields[name.strip()] = value.strip()
    if any(k not in fields for k in ("key", "ts", "nonce", "sig")):
        raise FeedError(401, "auth", "認証ヘッダに必須要素がありません。")
    return fields


def authenticate(
    headers: "Dict[str, str]", *, method: str, path: str, query: "Dict[str, str]",
    secret: bytes, key_id: str, nonces: NonceCache, now: int,
) -> None:
    """署名・鮮度・nonce を検証する。**端末に触れる前に**必ず通す。"""
    lookup = {str(k).lower(): v for k, v in headers.items()}
    fields = _parse_authorization(lookup.get("authorization", ""))

    if not hmac.compare_digest(fields["key"], key_id):
        raise FeedError(401, "auth", "鍵 ID が一致しません。")
    try:
        ts = int(fields["ts"])
    except ValueError:
        raise FeedError(401, "auth", "ts が整数ではありません。") from None
    if abs(int(now) - ts) > MAX_TIMESTAMP_SKEW_SECONDS:
        raise FeedError(401, "auth", "ts が鮮度窓の外です。")

    expected = hmac.new(
        secret,
        canonical_string(method, path, query, ts=ts, nonce=fields["nonce"]).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, fields["sig"]):
        raise FeedError(401, "auth", "署名が一致しません。")

    if not nonces.claim(fields["nonce"], now=int(now)):
        raise FeedError(401, "auth", "nonce が再使用されました。")


def load_secret(env: str = SECRET_ENV) -> bytes:
    """秘密を環境変数から読む（**ここが唯一の供給元**）。"""
    raw = os.environ.get(env, "")
    if len(raw) < MIN_SECRET_LENGTH:
        raise FeedError(
            500, "config",
            f"環境変数 {env} が未設定か短すぎます（{MIN_SECRET_LENGTH} 文字以上）。",
        )
    return raw.encode("utf-8")


def validate_bind(host: str) -> str:
    """待ち受けアドレスを特定 IF に限定する（全 IF への公開を拒む）。"""
    if host in _FORBIDDEN_BINDS:
        raise FeedError(
            500, "config",
            f"全インタフェースへの bind は禁止です: {host!r}。特定 IF を指定してください。",
        )
    return host


# ---------------------------------------------------------------------
# 端末の読み取り（epoch → datetime の変換を知る唯一の関数）
# ---------------------------------------------------------------------

def read_tick_window(
    mt5: Any, *, symbol: str, from_msc: int, to_msc: "Optional[int]", max_rows: int
) -> Any:
    """端末から生ティックを読む。**読み取りのみ**。

    epoch(ms) → 端末が要求する datetime への変換を知るのは本関数だけである（12h ずれ罠の
    閉じ込め）。変換の意味論は **V-1 で確定するまで仮説**であり、候補は 3 通りある:
    (1) naive datetime を端末がサーバ時刻として解釈する、(2) ローカル時刻として解釈する、
    (3) tz-aware を UTC へ正規化する。ここでは (1) を採り、**naive** な datetime を渡す
    （tz-aware を渡すとライブラリ側でローカル時刻換算が入り、環境依存の 12 時間ずれを生む）。
    V-1 で確定したら**この関数 1 つだけ**を直す。
    """
    if not mt5.symbol_select(symbol, True):
        raise FeedError(
            502, "terminal", f"symbol_select({symbol!r}) が失敗しました。",
            mt5.last_error(),
        )

    start = dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=int(from_msc))
    if to_msc is None:
        # 上端なし: 件数指定で読む。切り詰め検出のため 1 件多く要求する。
        rows = mt5.copy_ticks_from(symbol, start, max_rows + 1, mt5.COPY_TICKS_INFO)
    else:
        end = dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=int(to_msc))
        rows = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_INFO)

    if rows is None:
        raise FeedError(
            502, "terminal", "端末がティックを返しませんでした。", mt5.last_error()
        )
    return rows


def _server_name(mt5: Any) -> str:
    info = mt5.account_info()
    return "" if info is None else str(getattr(info, "server", ""))


# ---------------------------------------------------------------------
# 要求の処理（HTTP の配管から分離する＝ソケット無しで検定できる）
# ---------------------------------------------------------------------

def _int_arg(query: "Dict[str, str]", name: str) -> int:
    try:
        return int(query[name])
    except (KeyError, ValueError):
        raise FeedError(400, "argument", f"{name} が整数ではありません。") from None


def _ticks_response(mt5: Any, query: "Dict[str, str]") -> Response:
    symbol = query.get("symbol", "")
    if not symbol:
        raise FeedError(400, "argument", "symbol がありません。")

    from_msc = _int_arg(query, "from_msc")
    raw_to = query.get("to_msc", "")
    to_msc = None if raw_to == "" else _int_arg(query, "to_msc")
    max_rows = _int_arg(query, "max_rows")
    if max_rows <= 0 or max_rows > MAX_ROWS_LIMIT:
        raise FeedError(
            400, "argument", f"max_rows は 1〜{MAX_ROWS_LIMIT} の範囲で指定してください。"
        )
    if to_msc is not None and to_msc < from_msc:
        raise FeedError(400, "argument", "to_msc が from_msc より前です。")

    rows = read_tick_window(
        mt5, symbol=symbol, from_msc=from_msc, to_msc=to_msc, max_rows=max_rows
    )
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]

    latest = int(rows["time_msc"][-1]) if len(rows) else int(from_msc)
    headers = {
        "Content-Type": "application/octet-stream",
        "X-MT5-Count": str(len(rows)),
        "X-MT5-Dtype": json.dumps(rows.dtype.descr),
        "X-MT5-Latest-Msc": str(latest),
        "X-MT5-Truncated": "1" if truncated else "0",
        "X-MT5-Server": _server_name(mt5),
    }
    return Response(status=200, headers=headers, body=rows.tobytes())


def handle_request(
    target: str, headers: "Dict[str, str]", *, mt5: Any, secret: bytes, key_id: str,
    nonces: NonceCache, now: int,
) -> Response:
    """1 要求を処理して応答を返す（HTTP の配管を含まない＝そのまま検定できる）。

    順序に意味がある: **認証は端末に触れるより先**である。認証前に端末を触ると、
    未認証の相手にライブ口座の状態を触らせる経路ができる。
    """
    parsed = urlparse(target)
    path = parsed.path
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))

    try:
        authenticate(
            headers, method="GET", path=path, query=query,
            secret=secret, key_id=key_id, nonces=nonces, now=now,
        )
        if path not in ENDPOINTS:
            raise FeedError(404, "not_found", f"未知のパスです: {path}")
        if path == "/health":
            # 端末に触れない。生存確認で発注口座を触らないため。
            body = json.dumps({"ok": True}).encode("utf-8")
            return Response(200, {"Content-Type": "application/json"}, body)
        return _ticks_response(mt5, query)
    except FeedError as exc:
        return Response(
            exc.status, {"Content-Type": "application/json"}, exc.payload()
        )


# ---------------------------------------------------------------------
# HTTP の配管（単一スレッド）
# ---------------------------------------------------------------------

def make_handler(*, mt5: Any, secret: bytes, key_id: str, nonces: NonceCache):
    """要求処理を ``BaseHTTPRequestHandler`` へ結線する（配管だけを持つ）。"""

    class _Handler(BaseHTTPRequestHandler):
        server_version = "mt5-tick-feed"
        sys_version = ""

        def do_GET(self):  # noqa: N802  （BaseHTTPRequestHandler の規約）
            import time

            response = handle_request(
                self.path, dict(self.headers), mt5=mt5, secret=secret,
                key_id=key_id, nonces=nonces, now=int(time.time()),
            )
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    return _Handler


def build_parser() -> argparse.ArgumentParser:
    """CLI（秘密は引数に置かない＝環境変数のみ）。"""
    parser = argparse.ArgumentParser(
        description="MT5 端末の生ティックを HTTP で配る（読み取りのみ・発注 API 不使用）。"
    )
    parser.add_argument("--bind", default=DEFAULT_BIND,
                        help=f"待ち受けアドレス（既定 {DEFAULT_BIND}・全 IF は禁止）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"待ち受けポート（既定 {DEFAULT_PORT}）")
    parser.add_argument("--key-id", required=False, default="mt5-bridge",
                        help="許可する鍵 ID")
    return parser


def main(argv=None, mt5: Any = None) -> int:
    """常駐して要求を捌く。``--help`` は MetaTrader5 が無い環境でも成功する（A-6）。"""
    args = build_parser().parse_args(argv)
    try:
        host = validate_bind(args.bind)
        secret = load_secret()
    except FeedError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 2

    if mt5 is None:
        import MetaTrader5 as mt5  # 遅延 import: ここまでは端末が無くても動く。

    if not mt5.initialize():
        sys.stderr.write(f"mt5.initialize() が失敗しました（{mt5.last_error()}）\n")
        return 3
    try:
        handler = make_handler(
            mt5=mt5, secret=secret, key_id=args.key_id, nonces=NonceCache()
        )
        server = HTTPServer((host, args.port), handler)
        sys.stderr.write(f"listening on {host}:{args.port}\n")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            sys.stderr.write("stopping\n")
        finally:
            server.server_close()
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
