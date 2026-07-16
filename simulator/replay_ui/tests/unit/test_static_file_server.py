"""StaticFileServer の単体回帰（ISSUE-094 🟡-8: 殻からの静的配信＋トラバーサル防御の抽出）。

serve_replay の Handler から静的資産配信（dual-root 許可集合）とパストラバーサル防御
（``_resolve_under`` / is_relative_to 境界一致）を独立クラス StaticFileServer へ抽出した。
本テストは抽出後クラスが (1) web_dir 優先解決、(2) shared_js_root フォールバック、
(3) dual-root symlink 許可、(4) 生 ``..`` / 接頭辞共有兄弟への逸脱拒否、(5) 未知パス None を
満たすことを HTTP を介さず直接固定する（挙動＝配信面は不変）。
"""
from __future__ import annotations

import pytest

from simulator.replay_ui.framework.static_file_server import StaticFileServer


@pytest.fixture
def dual_root(tmp_path):
    """web_dir（replay 根）と shared（indicator_ui web 根）を実ファイルで用意する。"""
    web = tmp_path / "replay_web"
    shared = tmp_path / "shared_js"
    (web / "js").mkdir(parents=True)
    (web / "js" / "replay.js").write_text("REPLAY_SPECIFIC", encoding="utf-8")
    (web / "index.html").write_text("<html>", encoding="utf-8")
    for sub in ("js", "css", "vendor"):
        (shared / sub).mkdir(parents=True)
    (shared / "js" / "shared_only.js").write_text("SHARED_ONLY", encoding="utf-8")
    # web_dir/js 配下 symlink（→ shared 実体）: resolve 後は shared 配下だが dual-root で許可。
    (web / "js" / "linked.js").symlink_to(shared / "js" / "shared_only.js")
    # 接頭辞共有の兄弟に機密（`..` 逸脱の CWE-22 標的）。
    secret = tmp_path / "replay_web_SECRET"
    secret.mkdir()
    (secret / "leak.txt").write_text("TOP_SECRET", encoding="utf-8")
    return StaticFileServer(web.resolve(), shared.resolve())


def test_root_resolves_to_index_html(dual_root):
    fp = dual_root.resolve("/")
    assert fp is not None and fp.name == "index.html"


def test_web_dir_specific_file_resolved(dual_root):
    fp = dual_root.resolve("/js/replay.js")
    assert fp is not None
    assert fp.read_text(encoding="utf-8") == "REPLAY_SPECIFIC"


def test_shared_only_file_falls_back_to_shared_root(dual_root):
    fp = dual_root.resolve("/js/shared_only.js")
    assert fp is not None
    assert fp.read_text(encoding="utf-8") == "SHARED_ONLY"


def test_symlink_into_shared_allowed_via_dual_root(dual_root):
    fp = dual_root.resolve("/js/linked.js")
    assert fp is not None
    assert fp.read_text(encoding="utf-8") == "SHARED_ONLY"


def test_prefix_sibling_traversal_is_rejected(dual_root):
    # 区切り境界を見ない prefix 一致で `replay_web_SECRET` へ逸脱する CWE-22 を封じる。
    assert dual_root.resolve("/../replay_web_SECRET/leak.txt") is None


def test_unknown_path_returns_none(dual_root):
    assert dual_root.resolve("/js/does_not_exist.js") is None


def test_content_type_mapping(dual_root):
    fp = dual_root.resolve("/js/replay.js")
    assert dual_root.content_type(fp) == "application/javascript"
    idx = dual_root.resolve("/")
    assert dual_root.content_type(idx) == "text/html"
