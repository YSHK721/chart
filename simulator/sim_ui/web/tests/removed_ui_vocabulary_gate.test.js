// 撤去した UI 出口の語彙が front / CSS に 1 件も残っていないことの機械強制（Phase 9 S1）。
//
// なぜ検定にするか: 「消した」は宣言では守れない。条件ビルダー・建玉変更・候補供給は
// **UI 出口だけ**を落とす撤去であり、API 側（`/sim/ea-series`・`strategy` ブロック）は
// 存続する。したがって front に呼び出しの残骸が 1 行でも残ると、動く経路として静かに
// 復活し得る。ソーステキストを走査して、その語彙を機械的に 0 件に固定する。
//
// 走査対象は**実行されるコードと配信される CSS**（`js/adapter/front/*.js` と `css/*.css`）。
// コメントも対象に含める——「説明として残った語」は次の担当者にとって再導入の手引きになる。
//
// 固定する不変条件:
//   1. front の各モジュールに撤去語彙が 0 件。
//   2. 配信 CSS に撤去クラスが 0 件。
//   3. 検出器そのものが機能する（変異を注入すると検出できる＝空振りしていない）。走査対象が
//      0 件でも「違反 0 件」に見えてしまうため、**走査対象が空でないこと**を先に固定する
//      （実測: 収集条件を壊すと front も CSS も 0 件になり、全検定が無言で緑になった）。
//   4. 投入フォームの CSS 選択子がパネル id の配下に閉じている（結果ビューアへの波及 0）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONT_DIR = join(HERE, "..", "js", "adapter", "front");
const CSS_DIR = join(HERE, "..", "css");

const FRONT_FILES = readdirSync(FRONT_DIR).filter((f) => f.endsWith(".js"));
const CSS_FILES = readdirSync(CSS_DIR).filter((f) => f.endsWith(".css"));

/** 撤去した UI 出口の語彙（front・§19.2 の D-1〜D-10）。 */
const REMOVED_FRONT_VOCABULARY = [
  // 買い/売り条件ビルダー（行の器・側ブロック・投入キー）
  /exec-cond-row/,
  /exec-side/,
  /entry_long/,
  /entry_short/,
  /setIndicatorCandidates/,
  /onEaChange/,
  // 建玉変更（トレーリング / 部分決済）
  /trailing/i,
  /partial/i,
  /TRAIL/,
  // 候補供給（`/sim/ea-series` の front 消費者）
  /ea-series/,
  /EA_SERIES/,
  /loadEaSeries/,
  /onExpertChange/,
];

/** 撤去した CSS クラス（条件行・側ブロック・行追加/削除・建玉変更）。 */
const REMOVED_CSS_CLASSES = [
  "exec-cond-row", "exec-side", "exec-rows", "exec-add", "exec-del",
  "exec-trailing-on", "exec-partial-on", "exec-trail", "exec-partial",
];

/** 投入フォームの整形だけを持つ配信 CSS（結果ビューアの見た目には 1 つも当ててはならない）。 */
const RUN_FORM_CSS = "sim_run_form.css";
/** 投入フォームの面（この id の配下だけに選択子を閉じる）。 */
const FORM_PANEL_IDS = [
  "#simTesterPanel", "#simEaInputsPanel", "#simSchemaFallbackPanel", "#simRunActionPanel",
  // Phase 9 段階 3（§19.6）: 実行状態の掲示面。掲示専用（操作要素 0）だが、面である以上
  // 整形は同じ規律に従う——選択子はこの id の配下に閉じる。
  "#simRunStatusPanel",
];

/** ソース 1 本に残っている撤去語彙を列挙する（0 件が合格）。 */
function removedVocabularyOffenses(src, patterns) {
  const offenses = [];
  for (const pattern of patterns) {
    const hit = src.match(pattern);
    if (hit) offenses.push(`${pattern} → ${hit[0]}`);
  }
  return offenses;
}

// --- 3. 検出器の自己検定（先に置く: 空振りする検出器で 1・2 を主張しない）-----------

test("the removal detector actually sees a re-introduced vocabulary (自己検定)", () => {
  // 変異 1 点: 条件行の器を front へ戻した状態（実際に起きうる復活の形）。
  const mutated = 'const row = el("div", { className: "exec-cond-row" });';
  assert.ok(removedVocabularyOffenses(mutated, REMOVED_FRONT_VOCABULARY).length > 0,
    "検出器が撤去語彙の再導入を見逃しています（この検定が空振りなら下の走査は無意味）");
  // 撤去後に残る正当な書き方は 1 件も挙げない（過検出で通常の実装を禁じない）
  const proper = 'const body = { backtest };\nif (settings != null) body.settings = settings;';
  assert.deepEqual(removedVocabularyOffenses(proper, REMOVED_FRONT_VOCABULARY), []);
});

// --- 1. front に撤去語彙が 0 件 --------------------------------------------------

test("no front module keeps a removed UI-outlet vocabulary (Phase 9 S1)", () => {
  assert.ok(FRONT_FILES.length > 0, "front モジュールを 1 本も収集できていません（この走査は空振りです）");
  const offenders = [];
  for (const name of FRONT_FILES) {
    const src = readFileSync(join(FRONT_DIR, name), "utf8");
    for (const offense of removedVocabularyOffenses(src, REMOVED_FRONT_VOCABULARY)) {
      offenders.push(`${name}: ${offense}`);
    }
  }
  assert.deepEqual(offenders, []);
});

// --- 2. 配信 CSS に撤去クラスが 0 件 ---------------------------------------------

test("no shipped stylesheet keeps a removed UI-outlet class", () => {
  assert.ok(CSS_FILES.length > 0, "配信 CSS を 1 枚も収集できていません（この走査は空振りです）");
  const offenders = [];
  for (const name of CSS_FILES) {
    const src = readFileSync(join(CSS_DIR, name), "utf8");
    for (const cls of REMOVED_CSS_CLASSES) {
      if (src.includes(cls)) offenders.push(`${name}: .${cls}`);
    }
  }
  assert.deepEqual(offenders, []);
});

// --- 4. 投入フォーム CSS の波及遮断（Phase 9 S6・裁定 B と同じ理由）------------------
// 投入フォームの整形は `?job=` **無し**で開いた入口だけに当たる。素の要素（body / input /
// select / button）や共有クラスへ当てると、同じ文書で開く結果ビューアの見た目まで動く。
// 選択子の先頭が面の id であることを機械的に固定する（宣言では守れない）。

/** 宣言ブロックの選択子だけを取り出す（@media 等の at-rule と中身の宣言は除く）。 */
function selectorsOf(css) {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out = [];
  for (const [, head] of withoutComments.matchAll(/([^{}]+)\{/g)) {
    const selector = head.trim();
    if (!selector || selector.startsWith("@")) continue;
    for (const part of selector.split(",")) {
      const one = part.trim();
      if (one) out.push(one);
    }
  }
  return out;
}

test("the selector extractor sees both scoped and unscoped rules (自己検定)", () => {
  const found = selectorsOf("@media (max-width: 1px) { #simTesterPanel .x { color: red } }\nbutton { color: red }");
  assert.deepEqual(found.sort(), ["#simTesterPanel .x", "button"]);
});

test("every run-form rule is scoped under a form panel id (結果ビューアへの波及 0)", () => {
  const css = readFileSync(join(CSS_DIR, RUN_FORM_CSS), "utf8");
  const selectors = selectorsOf(css);
  assert.ok(selectors.length > 0, "選択子が 1 つも取れていません（この走査は空振りです）");
  const escaped = selectors.filter(
    (s) => !FORM_PANEL_IDS.some((id) => s === id || s.startsWith(`${id} `) || s.startsWith(`${id}[`) || s.startsWith(`${id}:`)),
  );
  assert.deepEqual(escaped, [],
    "投入フォーム CSS がパネル id の外へ出ています（結果ビューアの見た目まで動きます）");
});
