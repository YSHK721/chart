"""実 HTTP の増分ティック供給元（framework 層・stdlib の ``urllib`` のみ）。

本モジュールは :class:`marketdata.mt5_ticks.port.IncrementalTickSource` の**唯一の実装**で
ある。要求の組み立て・署名・応答の解析はいずれも :mod:`marketdata.mt5_ticks.wire`
（純粋な契約）へ委譲し、ここが持つのは「ソケットの配管」と「失敗の分類」だけである。
契約を持つと fake と実 HTTP で別々の契約が生まれる。

配管に置く安全側の既定（すべて外から緩められない形にする）:

``timeout`` 必須
    応答が来ない相手に常駐が張り付くと、供給は止まったまま「動いているように見える」。
    0 や ``None`` を渡す経路自体を作らない（:class:`ValueError`）。

リダイレクト不追従
    追えば「どこから取るか」を応答側が握る。供給元は運用者が決めるものである。

``Content-Length`` 上限
    body を読み切る前に大きさを見る。読んでから捨てるのでは、メモリを相手に預けたことになる。

失敗の分類（設計 §4）:
    HTTP の状態は :func:`marketdata.mt5_ticks.port.error_for_status` が**唯一の判断点**として
    分類する（401/429/502 は待てば直る＝バックオフ、400 と未知は待っても直らない＝Fail-Stop）。
    接続できない・時間切れは「待てば直りうる」側に置く。
    ヘッダと body の整合違反（:class:`marketdata.mt5_ticks.wire.WireError`）はそのまま送出する。
    ズレた body を解釈しないことが転送層の責務であり、再試行で直る種類の障害ではない。

依存宣言: stdlib＋:mod:`marketdata.mt5_ticks` 下位（wire / port）のみ。
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Callable, Dict, Optional
from urllib import error, request

from marketdata.mt5_ticks import wire
from marketdata.mt5_ticks.port import Mt5SupplyError, SupplyUnavailable, error_for_status

#: 応答を待つ上限（秒）。運用者が明示しない限りこの値が付く。
DEFAULT_TIMEOUT_SECONDS = 10.0
#: 1 応答で受け取ってよい body の上限（バイト）。既定は 100,000 行 × 48 バイトに十分な余裕。
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
#: 供給元のパス（VM 側の 2 エンドポイントのうち、コンテナが使うのはこれだけ）。
TICKS_PATH = "/ticks"


class _RefuseRedirects(request.HTTPRedirectHandler):
    """3xx を追わない（``None`` を返すと urllib はそのまま :class:`error.HTTPError` にする）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _new_nonce() -> str:
    """再生攻撃を拒むための使い捨て値（毎回異なる）。"""
    return secrets.token_hex(8)


class HttpTickSource:
    """署名付き HTTP で VM 側 feed から増分ティックを取る。"""

    def __init__(
        self,
        endpoint: str,
        *,
        key_id: str,
        secret: bytes,
        timeout: Any = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
        now: "Callable[[], float]" = time.time,
        nonce_factory: "Callable[[], str]" = _new_nonce,
    ):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError(
                f"timeout は正の秒数で指定してください（受け取った値: {timeout!r}）。"
                " 応答が来ない相手に常駐が張り付く経路を作らない。"
            )
        self.endpoint = str(endpoint).rstrip("/")
        self.key_id = str(key_id)
        self.max_bytes = int(max_bytes)
        self.timeout = float(timeout)
        self._secret = secret if isinstance(secret, bytes) else str(secret).encode("utf-8")
        self._now = now
        self._nonce = nonce_factory
        self._opener = request.build_opener(_RefuseRedirects)

    # -----------------------------------------------------------------
    # IncrementalTickSource
    # -----------------------------------------------------------------

    def fetch(
        self, *, symbol: str, from_msc: int, to_msc: "Optional[int]", max_rows: int
    ) -> wire.TickResponse:
        """``[from_msc, to_msc]`` のティックを 1 回の要求で取る（先読み・取り直しを持たない）。"""
        query = wire.build_query(
            symbol=symbol, from_msc=from_msc, to_msc=to_msc, max_rows=max_rows
        )
        req = request.Request(
            f"{self.endpoint}{TICKS_PATH}?{wire.sorted_query(query)}",
            method="GET",
            headers={"Authorization": self._authorization(query)},
        )
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                status = int(getattr(response, "status", response.getcode()))
                headers = dict(response.headers.items())
                body = self._read_bounded(response, headers)
        except error.HTTPError as exc:
            raise error_for_status(
                exc.code, dict(exc.headers.items()), exc.read()
            ) from exc
        except error.URLError as exc:
            raise SupplyUnavailable(
                f"MT5 供給元へ到達できません（{self.endpoint}）: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise SupplyUnavailable(
                f"MT5 供給元との通信が失敗しました（{self.endpoint}）: {exc}"
            ) from exc
        return wire.parse_response(status, headers, body)

    # -----------------------------------------------------------------
    # 配管
    # -----------------------------------------------------------------

    def _authorization(self, query: "Dict[str, str]") -> str:
        """要求ごとに新しい ts と nonce で署名する（秘密はここから外へ出ない）。"""
        ts = int(self._now())
        nonce = self._nonce()
        sig = wire.sign(
            self._secret, method="GET", path=TICKS_PATH, query=query, ts=ts, nonce=nonce
        )
        return wire.authorization_header(key_id=self.key_id, ts=ts, nonce=nonce, sig=sig)

    def _read_bounded(self, response: Any, headers: "Dict[str, str]") -> bytes:
        """宣言された大きさを先に見てから、上限を 1 バイト超えたら止める。"""
        declared = headers.get("Content-Length")
        if declared is not None and int(declared) > self.max_bytes:
            raise Mt5SupplyError(
                f"応答が上限を超えています: Content-Length={declared} > {self.max_bytes}。"
                " body を読まずに拒みます。"
            )
        body = response.read(self.max_bytes + 1)
        if len(body) > self.max_bytes:
            raise Mt5SupplyError(
                f"応答が上限（{self.max_bytes} バイト）を超えました（Content-Length なし）。"
            )
        return body
