"""転送契約（domain D・**依存ゼロ**・stdlib のみ）。

VM 側 feed は MT5 の生応答を ``ndarray.tobytes()`` のまま返す。無加工で返す設計にした理由は
「VM 側は定義を持たない」（列名も列順も VM が決めない）を守るためで、その代わり
**何バイトが何行なのかはヘッダだけが決める**。よってヘッダと body の整合が取れないときに
必ず止まることが、この層の唯一かつ最大の責務になる。

numpy に依存しない理由:
    本モジュールは domain 層であり stdlib のみに依存する。``X-MT5-Dtype``（numpy
    ``dtype.descr`` の JSON）を :mod:`struct` で解釈することで、numpy を持ち込まずに
    実バイト列と往復できる。``<`` 前置の struct 書式は numpy の非整列（packed）構造化配列と
    バイト配置が一致する。

``.npy`` を使わない理由:
    ``.npy`` 形式の決定性は未検証（設計 §4）。検証していない前提を転送契約に置かない。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import struct
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Tuple
from urllib.parse import urlencode

#: ``Authorization`` ヘッダのスキーム名（版を上げるときはここを変える）。
AUTH_SCHEME = "MT5B1"
#: 署名の鮮度窓（秒）。これを超える ts は再生攻撃として拒む。
MAX_TIMESTAMP_SKEW_SECONDS = 120
#: 応答が必ず持つヘッダ。
HEADER_COUNT = "X-MT5-Count"
HEADER_DTYPE = "X-MT5-Dtype"
HEADER_LATEST_MSC = "X-MT5-Latest-Msc"
HEADER_TRUNCATED = "X-MT5-Truncated"
HEADER_SERVER = "X-MT5-Server"
#: 応答から取り出す 3 列（列は捏造しない・ISSUE-447 方針 3）。
REQUIRED_FIELDS = ("time_msc", "bid", "ask")

#: numpy ``dtype.descr`` の型文字列 → :mod:`struct` の書式コード。
_STRUCT_CODE = {
    ("i", 1): "b", ("i", 2): "h", ("i", 4): "i", ("i", 8): "q",
    ("u", 1): "B", ("u", 2): "H", ("u", 4): "I", ("u", 8): "Q",
    ("f", 4): "f", ("f", 8): "d",
}


class WireError(RuntimeError):
    """転送契約が破れたことを表す（Fail-Stop）。ズレた body を解釈しない。"""


class TickResponse(NamedTuple):
    """解析済みの応答。``rows`` は ``(time_msc, bid, ask)`` の昇順並び。"""

    rows: "List[Tuple[int, float, float]]"
    count: int
    latest_msc: int
    truncated: bool
    server: str


# ---------------------------------------------------------------------
# 署名（正準化 → HMAC-SHA256）
# ---------------------------------------------------------------------

def sorted_query(query: "Mapping[str, Any]") -> str:
    """クエリをキー昇順で正準化する（順序差だけで 401 にしないため）。"""
    return urlencode(sorted((str(k), "" if v is None else str(v)) for k, v in query.items()))


def canonical_string(
    method: str, path: str, query: "Mapping[str, Any]", *, ts: int, nonce: str
) -> str:
    """署名対象の正準文字列 ``METHOD\\n/path\\n<sorted-query>\\n<ts>\\n<nonce>``。"""
    return "\n".join([method.upper(), path, sorted_query(query), str(int(ts)), str(nonce)])


def sign(
    secret: bytes, *, method: str, path: str, query: "Mapping[str, Any]", ts: int, nonce: str
) -> str:
    """正準文字列の HMAC-SHA256（hex）。秘密はここでしか使わない。"""
    payload = canonical_string(method, path, query, ts=ts, nonce=nonce)
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def authorization_header(*, key_id: str, ts: int, nonce: str, sig: str) -> str:
    """``Authorization`` ヘッダ値を組み立てる。"""
    return f"{AUTH_SCHEME} key={key_id},ts={int(ts)},nonce={nonce},sig={sig}"


def parse_authorization(header: str) -> "Dict[str, str]":
    """``Authorization`` ヘッダを 4 要素（key・ts・nonce・sig）へ分解する。"""
    text = (header or "").strip()
    prefix = AUTH_SCHEME + " "
    if not text.startswith(prefix):
        raise WireError(f"認証スキームが {AUTH_SCHEME} ではありません。")
    fields: "Dict[str, str]" = {}
    for part in text[len(prefix):].split(","):
        name, sep, value = part.partition("=")
        if not sep:
            raise WireError(f"認証ヘッダの要素が key=value ではありません: {part!r}")
        fields[name.strip()] = value.strip()
    missing = [k for k in ("key", "ts", "nonce", "sig") if k not in fields]
    if missing:
        raise WireError(f"認証ヘッダに必須要素がありません: {missing}")
    return fields


def is_fresh(ts: int, *, now: int) -> bool:
    """``ts`` が鮮度窓の内側か（境界 120 秒ちょうどは許容・それを超えたら拒む）。"""
    return abs(int(now) - int(ts)) <= MAX_TIMESTAMP_SKEW_SECONDS


# ---------------------------------------------------------------------
# 要求
# ---------------------------------------------------------------------

def build_query(
    *, symbol: str, from_msc: int, to_msc: "Optional[int]", max_rows: int
) -> "Dict[str, str]":
    """``/ticks`` のクエリを組み立てる（上端なしは空文字で表す）。"""
    return {
        "symbol": str(symbol),
        "from_msc": str(int(from_msc)),
        "to_msc": "" if to_msc is None else str(int(to_msc)),
        "max_rows": str(int(max_rows)),
    }


# ---------------------------------------------------------------------
# 応答
# ---------------------------------------------------------------------

def _field_struct(descr: "List[Any]") -> "Tuple[struct.Struct, List[str]]":
    """``dtype.descr`` を :mod:`struct` の読み取り器と列名へ変換する。"""
    if not isinstance(descr, list) or not descr:
        raise WireError(f"{HEADER_DTYPE} が descr の配列ではありません: {descr!r}")
    orders: "set[str]" = set()
    codes: "List[str]" = []
    names: "List[str]" = []
    for entry in descr:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise WireError(f"{HEADER_DTYPE} の要素が (name, typestr) ではありません: {entry!r}")
        name, typestr = entry
        if not isinstance(typestr, str) or len(typestr) < 3:
            raise WireError(f"{HEADER_DTYPE} の型文字列が解釈できません: {typestr!r}")
        order, kind, size = typestr[0], typestr[1], typestr[2:]
        if not size.isdigit():
            raise WireError(f"{HEADER_DTYPE} の型文字列が解釈できません: {typestr!r}")
        code = _STRUCT_CODE.get((kind, int(size)))
        if code is None:
            raise WireError(f"未対応の型が含まれます: {typestr!r}")
        orders.add("<" if order in "<|=" else ">")
        codes.append(code)
        names.append(str(name))
    if len(orders) > 1:
        raise WireError("バイト順が混在した dtype は解釈しません。")
    return struct.Struct(orders.pop() + "".join(codes)), names


def dtype_itemsize(descr: "List[Any]") -> int:
    """``dtype.descr`` 1 要素あたりのバイト数。"""
    reader, _ = _field_struct(descr)
    return reader.size


def parse_response(status: int, headers: "Mapping[str, str]", body: bytes) -> TickResponse:
    """成功応答を解析する。整合が取れない場合は必ず :class:`WireError`。

    検査するのは 3 点だけである（設計 §4）: ``Count`` × itemsize と body 長の一致、
    :data:`REQUIRED_FIELDS`（time_msc・bid・ask）の存在、``Latest-Msc`` の存在。どれも欠けると
    「ズレた値が静かに台帳へ入る」経路になる。
    """
    if int(status) != 200:
        raise WireError(f"成功応答ではありません: status={status}")

    lookup = {str(k).lower(): v for k, v in headers.items()}

    raw_dtype = lookup.get(HEADER_DTYPE.lower())
    if raw_dtype is None:
        raise WireError(f"{HEADER_DTYPE} がありません。")
    try:
        descr = json.loads(raw_dtype)
    except ValueError as exc:
        raise WireError(f"{HEADER_DTYPE} が JSON として読めません: {exc}") from exc
    reader, names = _field_struct(descr)

    missing = [f for f in REQUIRED_FIELDS if f not in names]
    if missing:
        raise WireError(
            f"応答 dtype に必須列がありません: {missing}（必須 {list(REQUIRED_FIELDS)}）。"
        )

    raw_latest = lookup.get(HEADER_LATEST_MSC.lower())
    if raw_latest is None:
        raise WireError(f"{HEADER_LATEST_MSC} がありません（供給の連続性を判断できない）。")

    raw_count = lookup.get(HEADER_COUNT.lower())
    if raw_count is None:
        raise WireError(f"{HEADER_COUNT} がありません。")
    try:
        count = int(raw_count)
        latest_msc = int(raw_latest)
    except ValueError as exc:
        raise WireError(f"ヘッダの整数が読めません: {exc}") from exc

    expected_len = count * reader.size
    if len(body) != expected_len:
        raise WireError(
            f"{HEADER_COUNT}({count}) × itemsize({reader.size}) = {expected_len} バイトに対し"
            f" body は {len(body)} バイトです。"
        )

    idx = {name: i for i, name in enumerate(names)}
    i_msc, i_bid, i_ask = idx["time_msc"], idx["bid"], idx["ask"]
    rows = [
        (int(values[i_msc]), float(values[i_bid]), float(values[i_ask]))
        for values in reader.iter_unpack(body)
    ]

    truncated = str(lookup.get(HEADER_TRUNCATED.lower(), "0")).strip() not in ("", "0", "false")
    return TickResponse(
        rows=rows,
        count=count,
        latest_msc=latest_msc,
        truncated=truncated,
        server=str(lookup.get(HEADER_SERVER.lower(), "")),
    )
