"""`unified_ui/serve.sh` が router を loopback 限定でバインドすることを固定する（🔴-5a）。

裁定（基本設計書 §12.9・2026-08-11 依頼者承認済み）: 公開ポート 8000 は
**127.0.0.1 限定**でバインドする。

なぜ必要か（実測された壊れ方）:
    `unified_ui/router.py:433` は ``--host`` の既定を ``""``（＝全インターフェース）
    としている。serve.sh が ``--host`` を渡さないと、router は 0.0.0.0:8000 で待ち受け、
    **同一ネットワーク上の他ホストから UI と API に到達できる**。基本設計書 §6.4 は
    「公開 8000 のみ外部公開。内部ポートは loopback 限定」と書いているが、ここでいう
    「公開」はローカル開発機からの利用を指し（§8.4「ローカル開発のみ・開発＝本番」）、
    LAN への露出は意図していない。router 側は既に ``--host`` を受けるため、
    起動側で明示するだけで閉じられる。

配置の理由（新規ファイルにしている点）:
    本 Phase の改変許可は承認済み 4 ファイルに限られる（§12.4）。既存の
    `test_unified_serve_sim_core.py` へ追記すると許可外の既存ファイル改変になるため、
    検定は新規ファイルとして足す（検定の追加は新規ファイルで完結できる）。

方式: 起動せずにスクリプト本文を読む（8000 を実際に掴まない）。
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SERVE = _ROOT / "unified_ui" / "serve.sh"
_ROUTER = _ROOT / "unified_ui" / "router.py"

# コメント行を除いた実行文だけを見る（注記としての言及に反応しないため）。
_CODE = "\n".join(
    line
    for line in _SERVE.read_text(encoding="utf-8").splitlines()
    if not line.lstrip().startswith("#")
)


def test_routerをloopback限定でバインドする() -> None:
    """serve.sh が router へ ``--host 127.0.0.1`` を渡すこと。"""
    assert re.search(r'--host\s+(["\']?)127\.0\.0\.1\1', _CODE), (
        "router 起動に --host 127.0.0.1 が無い（既定は全 IF バインド＝LAN へ露出する）"
    )


def test_router側は既定で全インターフェースにバインドする() -> None:
    """本検定が守っている前提の記録（router を無改変とした根拠）。

    router の既定が loopback に変われば本検定の必要性は消えるが、そのときは
    ここが落ちて「前提が変わった」ことに気付ける。
    """
    source = _ROUTER.read_text(encoding="utf-8")
    assert re.search(r'add_argument\(\s*"--host"\s*,\s*default=""', source), (
        "router の --host 既定が変わった（本検定の前提を確認すること）"
    )


def test_公開ポートの宣言は8000のまま() -> None:
    """バインド先を絞る変更であって、ポート採番は変えていないこと。"""
    assert re.search(r"^PUBLIC_PORT=8000$", _CODE, re.MULTILINE)
