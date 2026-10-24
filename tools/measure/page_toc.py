"""公開レポートに「目次」と「いま読んでいる節」を示す上端の帯を差し込む（全レポート共通の単一の部品）。

使い方:
    from tools.measure.page_toc import inject_toc
    html = inject_toc(html)

- 目次の項目は、ページの h2 見出しから作る（見出しと目次を二重に書かない）。id の無い h2 には id を付ける。
- 目次は最初の </header> の直後（無ければ最初の </h1> の直後、それも無ければ <main> の直後）に置く。
- 帯は最初の <main の直前（無ければ目次の直前）に置く。
- 色は部品専用の変数（--toc-*）で持ち、ページの配色に依存しない。明暗の両方に対応する。
- 何度通しても同じ結果になる（差し込み済みなら一度外してから差し込み直す）。
"""
from __future__ import annotations

import html as _html
import re

MARK_START = "<!-- page-toc:start -->"
MARK_END = "<!-- page-toc:end -->"

_STYLE = """<style>
:root { --toc-bg: #ffffff; --toc-fg: #1f2622; --toc-muted: #5d6a63; --toc-rule: #d6ddd8; --toc-acc: #2f6f8f; --toc-soft: #eef3f6; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --toc-bg: #1b2220; --toc-fg: #e3e9e5; --toc-muted: #9aa8a0; --toc-rule: #2d3632; --toc-acc: #7fbcd8; --toc-soft: #23302d; } }
:root[data-theme="dark"] { --toc-bg: #1b2220; --toc-fg: #e3e9e5; --toc-muted: #9aa8a0; --toc-rule: #2d3632; --toc-acc: #7fbcd8; --toc-soft: #23302d; }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 10; background: var(--toc-bg); }
.ptoc-now { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 9; background: var(--toc-bg); border-bottom: 1px solid var(--toc-rule); color: var(--toc-fg); }
.ptoc-in { max-width: 52rem; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 8px; display: flex; gap: 12px; align-items: center; min-width: 0; }
.ptoc-now button { font: inherit; font-size: .85rem; font-weight: 700; padding: 3px 12px; border: 1px solid var(--toc-rule); border-radius: 999px; background: var(--toc-soft); color: var(--toc-fg); cursor: pointer; flex: none; }
.ptoc-now button[aria-expanded="true"] { border-color: var(--toc-acc); color: var(--toc-acc); }
.ptoc-now button:focus-visible, .ptoc a:focus-visible { outline: 2px solid var(--toc-acc); outline-offset: 2px; }
.ptoc-label { font-size: .92rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.ptoc-num { color: var(--toc-acc); font-weight: 700; margin-right: 8px; font-variant-numeric: tabular-nums; }
.ptoc-prog { height: 3px; background: var(--toc-rule); }
.ptoc-prog i { display: block; height: 100%; width: 0; background: var(--toc-acc); }
.ptoc-list { max-width: 52rem; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 4px 12px; max-height: 60vh; overflow-y: auto; }
.ptoc-box { background: var(--toc-bg); color: var(--toc-fg); border: 1px solid var(--toc-rule); border-radius: 6px; padding: 12px 18px; }
.ptoc-h { font-weight: 700; margin: 0 0 4px; }
.ptoc ol { margin: 0; padding-left: 2em; display: grid; gap: 2px; }
.ptoc a { color: var(--toc-fg); text-decoration: none; display: block; padding: 2px 6px; border-radius: 4px; }
.ptoc a:hover { background: var(--toc-soft); }
.ptoc a[aria-current="true"] { color: var(--toc-acc); font-weight: 700; background: var(--toc-soft); }
.ptoc li::marker { color: var(--toc-muted); font-variant-numeric: tabular-nums; }
h2[data-ptoc] { scroll-margin-top: calc(env(safe-area-inset-top, 0px) + 64px); }
@media (prefers-reduced-motion: no-preference) { html { scroll-behavior: smooth; } }
</style>"""

_BAR = """<div class="ptoc-now" id="ptoc-now">
  <div class="ptoc-in">
    <button type="button" id="ptoc-btn" aria-expanded="false" aria-controls="ptoc-list">目次</button>
    <span class="ptoc-label"><span class="ptoc-num" id="ptoc-num"></span><span id="ptoc-title">はじめに</span></span>
  </div>
  <div class="ptoc-prog" aria-hidden="true"><i id="ptoc-prog"></i></div>
  <nav class="ptoc ptoc-list" id="ptoc-list" aria-label="目次（移動）" hidden>{toc}</nav>
</div>"""

_BOX = """<nav class="ptoc ptoc-box" aria-labelledby="ptoc-h">
  <p class="ptoc-h" id="ptoc-h">目次</p>
  {toc}
</nav>"""

_SCRIPT = """<script>
(() => {
  const heads = [...document.querySelectorAll("h2[data-ptoc]")];
  const links = (id) => document.querySelectorAll(`.ptoc a[href="#${id}"]`);
  const num = document.getElementById("ptoc-num"), title = document.getElementById("ptoc-title");
  const prog = document.getElementById("ptoc-prog"), btn = document.getElementById("ptoc-btn");
  const list = document.getElementById("ptoc-list"), bar = document.getElementById("ptoc-now");
  let current = null;
  function update() {
    const line = bar.getBoundingClientRect().bottom + 24;
    let idx = -1;
    heads.forEach((h, i) => { if (h.getBoundingClientRect().top <= line) idx = i; });
    const id = idx >= 0 ? heads[idx].id : null;
    if (id !== current) {
      if (current) links(current).forEach((a) => a.removeAttribute("aria-current"));
      current = id;
      if (id) {
        links(id).forEach((a) => a.setAttribute("aria-current", "true"));
        num.textContent = `${idx + 1} / ${heads.length}`;
        title.textContent = heads[idx].textContent;
      } else { num.textContent = ""; title.textContent = "はじめに"; }
    }
    const max = document.documentElement.scrollHeight - innerHeight;
    prog.style.width = `${max > 0 ? Math.min(100, scrollY / max * 100) : 0}%`;
  }
  let pending = false;
  addEventListener("scroll", () => { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; update(); }); } }, { passive: true });
  addEventListener("resize", update);
  btn.addEventListener("click", () => { const open = list.hidden; list.hidden = !open; btn.setAttribute("aria-expanded", String(open)); });
  list.addEventListener("click", (e) => { if (e.target.closest("a")) { list.hidden = true; btn.setAttribute("aria-expanded", "false"); } });
  update();
})();
</script>"""

_H2 = re.compile(r"<h2(\s[^>]*)?>(.*?)</h2>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def _strip(html: str) -> str:
    """差し込み済みの部品を外し、h2 に付けた目印も外す（何度通しても同じ結果にするため）。"""
    while MARK_START in html:
        a = html.index(MARK_START)
        b = html.index(MARK_END, a) + len(MARK_END)
        html = html[:a] + html[b:]
    return html.replace(" data-ptoc", "")


def headings(html: str):
    """[(id, 見出しの文字)]。id の無い h2 には sec-N を付けた html も返す。"""
    out = []
    n = 0

    def repl(m):
        nonlocal n
        n += 1
        attrs = m.group(1) or ""
        text = _html.unescape(_TAG.sub("", m.group(2))).strip()
        mid = re.search(r'\bid="([^"]+)"', attrs)
        hid = mid.group(1) if mid else f"sec-{n}"
        if not mid:
            attrs = f' id="{hid}"' + attrs
        out.append((hid, text))
        return f"<h2{attrs} data-ptoc>{m.group(2)}</h2>"

    return _H2.sub(repl, html), out


def inject_toc(html: str, scan=headings) -> str:
    """目次・上端の帯・スクリプトを差し込んだ html を返す。h2 が 2 つ未満なら何もしない。

    scan: 見出しの走査（観測の境界・計算量検定の注入点）。
    """
    html = _strip(html)
    marked, heads = scan(html)
    if len(heads) < 2:
        return html
    toc = "<ol>" + "".join(f'<li><a href="#{i}">{_html.escape(t)}</a></li>' for i, t in heads) + "</ol>"
    wrap = lambda s: f"{MARK_START}\n{s}\n{MARK_END}"  # 改行も目印の内側に入れる（外すと元に戻る）
    box = wrap(_BOX.format(toc=toc))
    bar = wrap(_STYLE + _BAR.format(toc=toc))
    for anchor in ("</header>", "</h1>"):
        k = marked.find(anchor)
        if k >= 0:
            k += len(anchor)
            marked = marked[:k] + box + marked[k:]
            break
    else:
        k = marked.find("<main")
        k = marked.index(">", k) + 1 if k >= 0 else 0
        marked = marked[:k] + box + marked[k:]
    k = marked.find("<main")
    if k < 0:
        k = marked.find(box)
    marked = marked[:k] + bar + marked[k:]
    return marked + wrap(_SCRIPT)
