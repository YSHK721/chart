"""需要台帳 — 直近に実際へ要求された束を記録し、起動時に再演で温める（ISSUE-501 段階 2）。

なぜ要るか:
    テンプレート（束の定義）はブラウザ側 localStorage にあり、サーバは起動時点で「どの
    instance が要るか」を知り得ない。知らないままでは、起動後の初回要求が全素材の組み立て
    （増分ビルダの初期化・当てはめ・比較集合）を**利用者を待たせながら**行うことになる。
    直近の実要求の束をディスクへ記録しておけば、起動直後にその束を 1 回だけ再演して
    プロセス内キャッシュ（MaterialStore / SheetState / 増分ビルダ）を温められる——
    再演で発行した計算は次の実要求がそのまま使う（発行 − 使用 = 0 の範囲は「束が
    前回から不変」のとき。束が変わっていれば差分だけが初回要求で作られる）。

記録の規約:
    - 記録するのは束の定義（dataset_ref / chart_timeframe / instances）だけ。
      known_state・mode の 2 欄は要求ごとの揮発量なので落とす。
    - 同じ束の再記録は書かない（書き込みの発行 − 束の変化 = 0。毎秒のポーリングで
      毎秒書くのは「作ってから捨てる」書き込みである）。
    - 記録失敗は握りつぶす（台帳は速さの装置。シート供給を落とさない）。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping

#: 台帳に写す要求フィールド（束の定義だけ・揮発量は載せない）。
_BUNDLE_FIELDS = ("dataset_ref", "chart_timeframe", "instances")


class DemandRecordingController:
    """controller のデコレータ: 成功した要求の束を台帳へ記録する（応答は素通し）。"""

    def __init__(self, inner: Any, *, ledger_path: "Path | str") -> None:
        self._inner = inner
        self._path = Path(ledger_path)
        self._last_written: "str | None" = None

    def handle(self, request: "Mapping[str, Any]") -> "dict[str, Any]":
        response = self._inner.handle(request)
        if response.get("ok") and request.get("instances"):
            self._record(request)
        return response

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._inner, attribute)   # 内側の面はそのまま見せる（検査用途）

    def _record(self, request: "Mapping[str, Any]") -> None:
        try:
            bundle = {field: request.get(field) for field in _BUNDLE_FIELDS}
            text = json.dumps(bundle, ensure_ascii=False, sort_keys=True)
            if text == self._last_written:
                return   # 束が不変なら書かない（無駄な書き込みを発行しない）
            _write_atomic(self._path, text)
            self._last_written = text
        except Exception:   # noqa: BLE001 — 台帳が書けなくてもシート供給は落とさない
            pass


def replay_recorded_demand(controller_factory, ledger_path: "Path | str") -> bool:
    """台帳の束を 1 回だけ再演してプロセス内キャッシュを温める。

    Returns:
        再演を発行したら True（台帳なし・読めない・空の束は False＝発行 0）。
    """
    try:
        bundle = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    except Exception:   # noqa: BLE001 — 台帳なし・破損は「温めない」だけ（正常系）
        return False
    if not isinstance(bundle, dict) or not bundle.get("instances"):
        return False
    controller_factory().handle({**bundle, "mode": "full"})
    return True


def start_warmup_thread(controller_factory, ledger_path: "Path | str") -> threading.Thread:
    """起動をブロックせずに再演する（統合 UI は `GET /` の 200 を待って router を起動する
    ため、待受け開始を再演で遅らせてはならない）。"""
    thread = threading.Thread(
        target=lambda: _replay_quietly(controller_factory, ledger_path),
        name="reach-sheet-warmup",
        daemon=True,
    )
    thread.start()
    return thread


def _replay_quietly(controller_factory, ledger_path: "Path | str") -> None:
    try:
        replay_recorded_demand(controller_factory, ledger_path)
    except Exception:   # noqa: BLE001 — 温め失敗は初回要求が従来どおり作るだけ
        pass


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
