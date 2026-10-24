"""page_toc.py の状態検証と計算量検定（CLAUDE.md の計算量テスト規約）。"""
from tools.measure import page_toc as T

PAGE = """<title>t</title><style>a{}</style>
<main>
<header><h1>題</h1><p>前書き</p></header>
<section><h2 id="h-a">一つ目</h2><p>x</p></section>
<section><h2>二つ<b>目</b></h2><p>y</p></section>
<section><h2 class="k">三つ目 &amp; 終わり</h2></section>
</main>"""


def test_toc_lists_every_h2_in_order_and_adds_missing_ids():
    out = T.inject_toc(PAGE)
    assert '<a href="#h-a">一つ目</a>' in out
    assert '<a href="#sec-2">二つ目</a>' in out
    assert '<a href="#sec-3">三つ目 &amp; 終わり</a>' in out
    assert '<h2 id="sec-2" data-ptoc>' in out and '<h2 id="sec-3" class="k" data-ptoc>' in out


def test_box_follows_header_and_bar_precedes_main():
    out = T.inject_toc(PAGE)
    assert out.index("</header>") < out.index('class="ptoc ptoc-box"') < out.index('id="h-a"')
    assert out.index('id="ptoc-now"') < out.index("<main>")


def test_injection_is_idempotent():
    once = T.inject_toc(PAGE)
    assert T.inject_toc(once) == once


def test_pages_with_fewer_than_two_headings_are_untouched():
    p = "<main><h1>x</h1><h2>only</h2></main>"
    assert T.inject_toc(p) == p


def test_falls_back_to_h1_when_there_is_no_header():
    p = "<main><h1>題</h1><h2>a</h2><h2>b</h2></main>"
    out = T.inject_toc(p)
    assert out.index("</h1>") < out.index('class="ptoc ptoc-box"')


def test_class_based_headings_for_pages_without_h2():
    p = ('<main><h1>t</h1><div class="ftitle big">一</div><div class="other">x</div>'
         '<div class="ftitle">二</div></main>')
    out = T.inject_toc(p, tag="div", cls="ftitle")
    assert '<a href="#sec-1">一</a>' in out and '<a href="#sec-2">二</a>' in out
    assert '<div class="other">x</div>' in out and out.count(" data-ptoc>") == 2
    assert T.inject_toc(out, tag="div", cls="ftitle") == out


def test_unwrap_removes_the_publish_skeleton_only():
    sk = ('<!doctype html><html><head><meta charset=utf8><style>x{}</style></head><body>\n'
          '<title>t</title><main><h2>a</h2><h2>b</h2></main>\n</body></html>')
    body = T.unwrap_skeleton(sk)
    assert body.startswith("<title>t</title>") and "</body></html>" not in body and "<!doctype" not in body
    assert T.unwrap_skeleton(body) == body
    # 差し込み済みで </body></html> の後ろに部品があるものも、枠だけ外れて部品は付け直せる
    once = T.inject_toc(body)
    broken = sk.replace("</main>\n</body></html>", "</main>\n</body></html>") + "<!-- page-toc:start -->x<!-- page-toc:end -->"
    assert T.inject_toc(T.unwrap_skeleton(broken)).count('class="ptoc ptoc-box"') == 1
    assert "</body></html>" not in once


# ---- 計算量検定（注入点 scan を数える） ----

class ScanSpy:
    def __init__(self):
        self.issued = 0

    def __call__(self, html, tag="h2", cls=None):
        self.issued += 1
        return T.headings(html, tag, cls)


def _count(n_heads):
    body = "".join(f"<section><h2>見出し{i}</h2></section>" for i in range(n_heads))
    spy = ScanSpy()
    out = T.inject_toc(f"<main><header><h1>t</h1></header>{body}</main>", scan=spy)
    used = 1 if "ptoc-box" in out else 0  # 走査の結果は目次の生成に 1 回使われる
    return spy.issued, used


def test_headings_are_scanned_once_and_the_result_is_used():
    issued, used = _count(5)
    assert issued - used == 0


def test_scan_count_does_not_grow_with_number_of_headings():
    assert _count(3)[0] == _count(60)[0]
