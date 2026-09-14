"""StaticFileServer — replay backend の静的資産配信＋パストラバーサル防御（framework 層）。

CLEAN_ARCH §6 / ISSUE-094 🟡-8: serve_replay の HTTP ハンドラから「静的資産配信（dual-root
許可集合）」と「パストラバーサル防御（``_resolve_under`` / is_relative_to 境界一致）」を
独立クラスへ抽出する。Handler は API ルーティングと本クラスへの委譲のみを担う（薄殻化）。
配信面・許可根・応答 byte は抽出前と不変（純粋な構造移動）。

許可根（multi-root ガード）:
  - web_dir: replay の web 根全体（index.html/js/css/vendor + replay 固有）。
  - shared_js_root の js/css/vendor サブツリー（最小権限。web 根全体は露出させない）。
  - 共有フロント供給パッケージの ``<pkg>/web/js``（実体の置き場。web_dir/js の symlink 先）。
    どのパッケージが供給側かは中立核の台帳 ``common.shared_web_roots`` が単独で持つ
    （ISSUE-502 C-4 3b。ここへ書き写すとライブ殻側の列挙と 2 重になる）。
パストラバーサル（``..``）は resolve() 後に許可根の配下を外れるため弾かれる
（is_relative_to の区切り境界一致で CWE-22 を封じる）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from common.shared_web_roots import shared_web_js_roots

# 静的配信の拡張子 → Content-Type（proto H 忠実・最小限定）。
_CONTENT_TYPES = {
    "html": "text/html",
    "js": "application/javascript",
    "mjs": "application/javascript",
    "css": "text/css",
    "json": "application/json",
}


class StaticFileServer:
    """web_dir 優先・shared_js_root フォールバックで multi-root ガード配下の実ファイルを解決・配信する。

    ``web_dir`` / ``shared_js_root`` は resolve 済みの :class:`~pathlib.Path`（または None）。
    None のとき当該根は許可集合から外れる（従来の web_dir 単独挙動と不変）。
    """

    def __init__(
        self, web_dir: "Optional[Path]", shared_js_root: "Optional[Path]"
    ) -> None:
        self.web_dir = web_dir
        self.shared_js_root = shared_js_root

    def _allowed_roots(self) -> tuple:
        """multi-root ガードで許可する根の集合を返す（web_dir 全体＋共有の3サブツリー＋供給根）。

        shared_js_root（=indicator_ui/web）全体を許可すると build.mjs/package.json/data/tests/
        node_modules/prototype 等まで配信面に露出するため、資産3サブツリー（js/css/vendor）だけを
        許可根にする（最小権限）。

        共有フロント（market_profile / chart_kernel …）は別パッケージ ``<pkg>/web/js`` に実体があり、
        consumer 側の js/ 配下 symlink がそこを指す。resolve() 後は当該サブツリー配下へ抜けるため、
        供給根を許可根へ加える。**どのパッケージが供給側かは台帳 ``common.shared_web_roots`` が
        単独で持つ**（ISSUE-502 C-4 3b）。ここで名前を列挙し直すと、ライブ殻
        （indicator_ui/api/framework/server.py）の許可根と 2 重の所有者になり、片方だけ足した日に
        その core だけ URL 同一のまま 404 になる（2026-09-06 実測）。

        供給根は ``shared_js_root``（=<indigators>/indicator_ui/web）の親の親、すなわち起動中ツリーの
        indigators 根から導く。``__file__`` からではなく**注入された根**から導くのは、テストが
        tmp ツリーを注入したときにその中で閉じるためである（ISSUE-279 の「起動中のツリーから
        解決する」規約と同じ理由）。
        """
        shared = self.shared_js_root
        if shared is None:
            return (self.web_dir,)
        return (
            self.web_dir,
            shared / "js",
            shared / "css",
            shared / "vendor",
            *shared_web_js_roots(shared.parents[1]),
        )

    @staticmethod
    def _resolve_under(join_root: "Optional[Path]", rel: str, allowed_roots) -> "Optional[Path]":
        """``join_root/rel`` を解決し、multi-root ガードを通った実ファイルのみ返す。

        resolve() 後の実パスが ``allowed_roots``（web_dir / shared_js_root サブツリー）のいずれかの
        配下にあり、かつ実ファイルのときのみ返す。単一ソース共有では web_dir/js 配下の
        シンボリックリンクが shared_js_root（=indicator_ui/web/js）を指すため、resolve() 後は
        shared_js_root 配下になる。multi-root ガードにより web_dir 経由の一次解決でそのまま許可される。
        パストラバーサル（``..``）は resolve 後に両ルート配下を外れるため弾かれる。
        join_root が None・全ルート不通過・非ファイルのときは None（呼び出し側が次ルート/404 へ）。
        """
        if join_root is None:
            return None
        fp = (join_root / rel).resolve()
        if not fp.is_file():
            return None
        for ar in allowed_roots:
            # 区切り境界一致（Path.is_relative_to, Python 3.9+）で CWE-22 を封じる。
            #   str.startswith は区切り境界を見ないため `.../replay_web` と接頭辞を共有する
            #   兄弟 `.../replay_web_SECRET` へ生 `..` で逸脱できてしまう（区切り境界なし
            #   prefix 一致）。is_relative_to は resolve() 後の実パスに対して境界単位で判定
            #   するため、正規の symlink（resolve 先が shared_js_root 配下）は許可され続ける。
            if ar is not None and fp.is_relative_to(ar):
                return fp
        return None

    def resolve(self, path: str) -> "Optional[Path]":
        """URL パスを multi-root ガード配下の実ファイルへ解決する（見つからなければ None）。

        ``/`` は index.html へ。replay web_dir を優先し、miss なら shared_js_root へ同一 rel で
        フォールバックする（symlink 化した資産は web_dir 経由で一次解決されるため、本フォールバックは
        indicator_ui のみに実体があるファイル用）。index.html は web_dir に実体があるため常に web_dir
        が優先され、共有元へは落ちない（per-app）。
        """
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        allowed = self._allowed_roots()
        fp = self._resolve_under(self.web_dir, rel, allowed)
        if fp is None:
            fp = self._resolve_under(self.shared_js_root, rel, allowed)
        return fp

    @staticmethod
    def content_type(fp: Path) -> str:
        """拡張子から Content-Type（charset 抜きの素）を返す（未知は text/plain）。"""
        return _CONTENT_TYPES.get(fp.suffix.lstrip("."), "text/plain")

    def serve(self, handler: Any, path: str) -> None:
        """``handler``（BaseHTTPRequestHandler）へ静的ファイル応答を書き出す（byte 不変）。

        解決失敗は 404（本文なし）。成功は 200 ＋ Content-Type（charset=utf-8）・Cache-Control:
        no-store・Content-Length ＋ 本文（proto H・抽出前の Handler._serve_static と同一）。
        """
        fp = self.resolve(path)
        if fp is None:
            handler.send_response(404)
            handler.end_headers()
            return
        ct = self.content_type(fp)
        body = fp.read_bytes()
        handler.send_response(200)
        handler.send_header("Content-Type", ct + "; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
