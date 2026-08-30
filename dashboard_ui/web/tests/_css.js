// dashboard の検定が共有する CSS の読み取り。
//
// なぜ切り出すか: 版面モックへの同期（ISSUE-463）で dashboard.css に `@media` が入り、
//   「`}` で分割して選択子を取る」という素朴な読み方が at-rule を選択子と誤認するようになった。
//   同じ読み方を index_html_contract と dashboard_theme_contrast の 2 か所へ手書き複製すると
//   必ず片方が取り残される（MEMORY: no-hand-duplication-single-source）。
//
// 解するのは本ファイルが実際に使う範囲（コメント / 入れ子 1 段の at-rule / 宣言）だけで、
//   CSS の完全なパーサではない。範囲外の記法が入ったら検定が落ちる側に倒す。

/** コメントを落とす（宣言の中身だけを見たいため）。 */
export function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/**
 * 波括弧を数えながら**スタイル規則だけ**を取り出す。
 *
 * at-rule（`@media …`）は前置きであって選択子ではないので規則としては返さず、
 * その内側の規則に `at` として付ける（＝ at-rule の中身を素通ししない）。
 *
 * @param {string} css
 * @returns {Array<{selector: string, body: string, at: string[]}>}
 */
export function styleRules(css) {
  const src = stripComments(css);
  const out = [];
  const stack = [];
  let buf = '';
  for (const char of src) {
    if (char === '{') {
      stack.push(buf.trim());
      buf = '';
    } else if (char === '}') {
      const prelude = stack.pop();
      const body = buf;
      buf = '';
      if (prelude !== undefined && !prelude.startsWith('@')) {
        out.push({ selector: prelude, body, at: stack.filter((s) => s.startsWith('@')) });
      }
    } else {
      buf += char;
    }
  }
  return out;
}

/** 宣言（プロパティと値の対）へ分解する。 */
export function declarations(ruleBody) {
  const out = [];
  let depth = 0;
  let chunk = '';
  const flush = () => {
    const text = chunk.trim();
    chunk = '';
    if (!text) return;
    const at = text.indexOf(':');
    if (at < 0) return;
    const prop = text.slice(0, at).trim();
    const value = text.slice(at + 1).trim();
    if (prop && value) out.push({ prop: prop.startsWith('--') ? prop : prop.toLowerCase(), value });
  };
  for (const char of ruleBody) {
    if (char === '(') depth += 1;
    if (char === ')') depth -= 1;
    if (char === ';' && depth === 0) flush();
    else chunk += char;
  }
  flush();
  return out;
}

/** 値が参照しているトークン名（`var(--x)` の `--x`）。 */
export function tokensIn(value) {
  return [...String(value).matchAll(/var\(\s*(--[\w-]+)/g)].map((m) => m[1]);
}
