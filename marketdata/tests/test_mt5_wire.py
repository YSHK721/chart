"""転送契約（署名・応答解析）の検定（ISSUE-447 段階 1 / 検定 E-4・E-5・B-4）。

VM 側 feed は MT5 の生応答（``ndarray.tobytes()``）を無加工で返す。よって「何バイト来たら
何行なのか」を決めるのはヘッダ ``X-MT5-Count`` と ``X-MT5-Dtype`` だけであり、ここが食い違えば
無言でズレた値が台帳へ入る。本検定は**整合が取れないときに必ず例外になる**ことを固定する。

本モジュールは numpy に依存しない（domain 層は stdlib のみ）。一方で本検定は**実際の
numpy 構造化配列**から body とヘッダを組み立てる。解析器が numpy の実バイト列と往復する
ことを、宣言ではなく実物で確かめるためである。
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from marketdata.mt5_ticks import wire
from marketdata.mt5_ticks.wire import WireError

#: MT5 ``copy_ticks_*`` が返す構造化配列と同じ形（情報ティック）。
_MT5_TICK_DTYPE = np.dtype([
    ("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"),
    ("volume", "<u8"), ("time_msc", "<i8"), ("flags", "<u4"), ("volume_real", "<f8"),
])


def _ticks(*specs) -> np.ndarray:
    arr = np.zeros(len(specs), dtype=_MT5_TICK_DTYPE)
    for i, (msc, bid, ask) in enumerate(specs):
        arr[i]["time"] = msc // 1000
        arr[i]["time_msc"] = msc
        arr[i]["bid"] = bid
        arr[i]["ask"] = ask
    return arr


def _headers(arr: np.ndarray, *, truncated: int = 0, **override) -> "dict[str, str]":
    h = {
        "X-MT5-Count": str(len(arr)),
        "X-MT5-Dtype": json.dumps(arr.dtype.descr),
        "X-MT5-Latest-Msc": str(int(arr["time_msc"][-1])) if len(arr) else "0",
        "X-MT5-Truncated": str(truncated),
        "X-MT5-Server": "OANDA-Japan MT5 Live",
    }
    h.update(override)
    return h


# =====================================================================
# 署名の正準化
# =====================================================================

def test_canonical_string_has_the_declared_five_line_shape():
    """正準文字列は ``METHOD\\n/path\\n<sorted-query>\\n<ts>\\n<nonce>``（設計 §4）。"""
    got = wire.canonical_string(
        "GET", "/ticks", {"symbol": "JP225", "from_msc": "1000"}, ts=1700, nonce="n1"
    )
    assert got == "GET\n/ticks\nfrom_msc=1000&symbol=JP225\n1700\nn1"


def test_canonical_string_sorts_the_query_so_parameter_order_cannot_break_the_signature():
    """クエリ順が違っても同じ正準文字列（順序差で 401 にしない）。"""
    a = wire.canonical_string("GET", "/ticks", {"b": "2", "a": "1"}, ts=1, nonce="n")
    b = wire.canonical_string("GET", "/ticks", {"a": "1", "b": "2"}, ts=1, nonce="n")
    assert a == b


def test_signature_changes_with_secret_timestamp_nonce_and_query():
    """署名は 4 要素すべてに依存する（どれかを無視していたら偽造できる）。"""
    base = dict(method="GET", path="/ticks", query={"symbol": "JP225"}, ts=1700, nonce="n1")
    sig = wire.sign(b"secret", **base)
    assert sig != wire.sign(b"other", **base)
    assert sig != wire.sign(b"secret", **{**base, "ts": 1701})
    assert sig != wire.sign(b"secret", **{**base, "nonce": "n2"})
    assert sig != wire.sign(b"secret", **{**base, "query": {"symbol": "US30"}})


def test_signature_is_deterministic_and_hex():
    sig = wire.sign(b"s", method="GET", path="/ticks", query={"a": "1"}, ts=1, nonce="n")
    assert sig == wire.sign(b"s", method="GET", path="/ticks", query={"a": "1"}, ts=1, nonce="n")
    assert len(sig) == 64 and set(sig) <= set("0123456789abcdef")


def test_authorization_header_round_trips_through_the_parser():
    header = wire.authorization_header(key_id="k1", ts=1700, nonce="n1", sig="ab" * 32)
    assert header.startswith(wire.AUTH_SCHEME + " ")
    parsed = wire.parse_authorization(header)
    assert parsed == {"key": "k1", "ts": "1700", "nonce": "n1", "sig": "ab" * 32}


@pytest.mark.parametrize("bad", ["", "Bearer x", "MT5B1", "MT5B1 key=k1,ts=1", "MT5B1 nope"])
def test_malformed_authorization_headers_are_rejected(bad):
    with pytest.raises(WireError):
        wire.parse_authorization(bad)


def test_freshness_window_is_120_seconds():
    """ts 差 > 120s は不可（設計 §4 の閾値をここ 1 箇所に持つ）。"""
    assert wire.MAX_TIMESTAMP_SKEW_SECONDS == 120
    assert wire.is_fresh(1000, now=1000 + 120) is True
    assert wire.is_fresh(1000, now=1000 - 120) is True
    assert wire.is_fresh(1000, now=1000 + 121) is False
    assert wire.is_fresh(1000, now=1000 - 121) is False


# =====================================================================
# 要求の組み立て
# =====================================================================

def test_request_query_carries_the_four_declared_parameters():
    q = wire.build_query(symbol="JP225", from_msc=1000, to_msc=None, max_rows=5000)
    assert q == {"symbol": "JP225", "from_msc": "1000", "to_msc": "", "max_rows": "5000"}


# =====================================================================
# 応答解析（正常）
# =====================================================================

def test_parse_response_extracts_the_three_columns_that_matter():
    """``time_msc`` / ``bid`` / ``ask`` を抜き、他の列は捨てる（列は捏造しない）。"""
    arr = _ticks((1000, 66020.1, 66035.1), (1001, 66020.2, 66035.2))
    got = wire.parse_response(200, _headers(arr), arr.tobytes())
    assert got.rows == [(1000, 66020.1, 66035.1), (1001, 66020.2, 66035.2)]
    assert got.count == 2
    assert got.latest_msc == 1001
    assert got.truncated is False
    assert got.server == "OANDA-Japan MT5 Live"


def test_parse_response_preserves_float_values_bit_for_bit():
    """往復で値が変質しない（丸めを挟まない）。"""
    arr = _ticks((1, 66018.366000001, 66028.685999999))
    got = wire.parse_response(200, _headers(arr), arr.tobytes())
    assert got.rows[0][1] == float(arr["bid"][0])
    assert got.rows[0][2] == float(arr["ask"][0])


# =====================================================================
# B-4 境界: 0 行 / truncated / max_rows ちょうど
# =====================================================================

def test_an_empty_response_is_valid_and_yields_no_rows():
    """B-4(1): 0 行応答は正常（例外にしない・書込対象が無いだけ）。"""
    arr = _ticks()
    got = wire.parse_response(200, _headers(arr), arr.tobytes())
    assert got.rows == [] and got.count == 0


def test_a_truncated_response_is_flagged_so_the_caller_can_keep_pulling():
    """B-4(2): ``X-MT5-Truncated=1`` が呼び出し側へ伝わる。"""
    arr = _ticks((1000, 1.0, 2.0))
    got = wire.parse_response(200, _headers(arr, truncated=1), arr.tobytes())
    assert got.truncated is True


def test_a_response_of_exactly_max_rows_is_parsed_without_special_casing():
    """B-4(3): 要求上限ちょうどでも解析は同じ（境界で分岐を作らない）。"""
    arr = _ticks(*[(1000 + i, 1.0, 2.0) for i in range(64)])
    got = wire.parse_response(200, _headers(arr, truncated=1), arr.tobytes())
    assert got.count == 64 and len(got.rows) == 64


# =====================================================================
# E-4 / E-5 異常系
# =====================================================================

def test_missing_time_msc_in_the_dtype_is_a_wire_error():
    """E-4: dtype に ``time_msc`` が無い → ``WireError``。"""
    arr = np.zeros(1, dtype=np.dtype([("time", "<i8"), ("bid", "<f8"), ("ask", "<f8")]))
    headers = {
        "X-MT5-Count": "1",
        "X-MT5-Dtype": json.dumps(arr.dtype.descr),
        "X-MT5-Latest-Msc": "1000",
        "X-MT5-Truncated": "0",
        "X-MT5-Server": "S",
    }
    with pytest.raises(WireError, match="time_msc"):
        wire.parse_response(200, headers, arr.tobytes())


@pytest.mark.parametrize("missing", ["bid", "ask"])
def test_missing_price_columns_in_the_dtype_are_wire_errors(missing):
    fields = [("time_msc", "<i8"), ("bid", "<f8"), ("ask", "<f8")]
    fields = [f for f in fields if f[0] != missing]
    arr = np.zeros(1, dtype=np.dtype(fields))
    headers = {
        "X-MT5-Count": "1", "X-MT5-Dtype": json.dumps(arr.dtype.descr),
        "X-MT5-Latest-Msc": "1", "X-MT5-Truncated": "0", "X-MT5-Server": "S",
    }
    with pytest.raises(WireError, match=missing):
        wire.parse_response(200, headers, arr.tobytes())


def test_count_that_disagrees_with_the_body_length_is_a_wire_error():
    """E-5: ``X-MT5-Count`` × itemsize ≠ body 長 → ``WireError``。"""
    arr = _ticks((1000, 1.0, 2.0), (1001, 1.0, 2.0))
    with pytest.raises(WireError):
        wire.parse_response(200, _headers(arr, **{"X-MT5-Count": "3"}), arr.tobytes())


def test_a_truncated_body_is_a_wire_error():
    """body が途中で切れていたら（TCP 切断等）解析しない。"""
    arr = _ticks((1000, 1.0, 2.0), (1001, 1.0, 2.0))
    with pytest.raises(WireError):
        wire.parse_response(200, _headers(arr), arr.tobytes()[:-4])


def test_missing_latest_msc_header_is_a_wire_error():
    """``X-MT5-Latest-Msc`` は必須（欠けたら供給の連続性を判断できない）。"""
    arr = _ticks((1000, 1.0, 2.0))
    headers = _headers(arr)
    del headers["X-MT5-Latest-Msc"]
    with pytest.raises(WireError, match="Latest-Msc"):
        wire.parse_response(200, headers, arr.tobytes())


@pytest.mark.parametrize("bad_dtype", ["not json", "[]", '[["time_msc"]]', '{"a":1}'])
def test_unusable_dtype_headers_are_wire_errors(bad_dtype):
    arr = _ticks((1000, 1.0, 2.0))
    with pytest.raises(WireError):
        wire.parse_response(200, _headers(arr, **{"X-MT5-Dtype": bad_dtype}), arr.tobytes())


def test_a_non_200_status_is_not_parsed_as_a_body():
    """異常応答を「0 行の正常応答」として飲み込まない。"""
    with pytest.raises(WireError):
        wire.parse_response(500, {}, b"")
