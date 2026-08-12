// 複製 0 の機械強制（import 元パス構造検定）。
//
// なぜ検定にするか: 「写さない」は宣言では守れない（実測済みの失敗が繰り返されている）。
// sim 表示層は report_ui の実体を **/sim/report-js/ から import して使う**のであって、
// 定数・構築規則を持ち込まない。持ち込んだ瞬間、パリティは静かにドリフトする。
// 本スイートはソーステキストを読んで、その構造を機械的に固定する。
//
// 固定する不変条件:
//   1. 合成根（F-1）は移植元の実体を `/sim/report-js/*` からだけ import する。
//   2. sim 表示層のどのファイルにも、移植元が持つ**表示規則の定義**が無い（0 件）。
//   3. v4 vendor（lightweight-charts.standalone.js）への参照が無い（NFR-07）。
//   4. lwc に触るのは F-3 だけ（DIP: View も合成根も lwc API 名を書かない）。
//   5. View / アダプタはグローバル `document` を掴まない（注入された doc だけを使う）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONT_DIR = join(HERE, "..", "js", "adapter", "front");

// 判定対象は**実行されるコード**のみ（コメントの説明文に移植元パスや v4 API 名が
// 事例として出るのは正当。同じ考え方を tools/tests/test_web_suites_ledger.py が採る）。
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")     // ブロックコメント
    .replace(/(^|[^:"'`\\])\/\/.*$/gm, "$1"); // 行コメント（URL の // は残す）
}

const read = (name) => stripComments(readFileSync(join(FRONT_DIR, name), "utf8"));
const FRONT_FILES = readdirSync(FRONT_DIR).filter((f) => f.endsWith(".js"));

const ROOT = "composition_root_front.js";
const RENDERER = "lwc5_chart_renderer.js";
const VIEW = "sim_display_view.js";
const SOURCE = "report_source_client.js";
const FRAME = "sim_frame_view.js";

const WEB_DIR = join(HERE, "..");
const REPORT_VIEW_HTML = readFileSync(join(WEB_DIR, "report_view.html"), "utf8");

/** import 文の from 指定を列挙する。 */
function importSpecifiers(src) {
  return [...src.matchAll(/^\s*import\s[^;]*?from\s+["']([^"']+)["'];/gms)].map((m) => m[1]);
}

// --- 1. 移植元は /sim/report-js/ からだけ引く ------------------------------------

test("the front layer ships exactly the five Phase 4 modules", () => {
  assert.deepEqual(FRONT_FILES.sort(), [ROOT, RENDERER, SOURCE, VIEW, FRAME].sort());
});

// --- 1b. style.css の波及遮断（裁定 B）------------------------------------------
// 移植元 style.css は**子文書（report_view.html）の中だけ**で読む。統合ページ側の
// コードが link すると、実測どおり既存 UI の body 背景・font・文字色が変わる。

test("no front module links the report_ui stylesheet into the host page", () => {
  for (const name of FRONT_FILES) {
    assert.ok(!read(name).includes("/sim/report-css/"),
      `${name} が統合ページへ移植元 style.css を持ち込んでいます（波及遮断の破れ）`);
  }
});

test("the child document is the only place that links style.css", () => {
  assert.ok(REPORT_VIEW_HTML.includes("/sim/report-css/style.css"),
    "report_view.html が移植元 style.css を link していません");
});

test("the child document links the sim-owned frame stylesheet (器の高さ)", () => {
  // 無いと style.css の縦 flex の中で器の高さが決まらず、3 窓が 2px に潰れる（実測）。
  assert.ok(REPORT_VIEW_HTML.includes("/sim/css/sim_display.css"));
});

test("the child document loads the shared v5 vendor (v4 は載せない)", () => {
  assert.ok(REPORT_VIEW_HTML.includes("/sim/vendor/lightweight-charts.js"));
  assert.ok(!REPORT_VIEW_HTML.includes("standalone.js"), "v4 バンドルを載せています");
});

test("the child document owns no report DOM (骨格は View が生成する)", () => {
  for (const id of ["price-chart", "paneBal", "paneDD", "tradeTable", "chartBadge"]) {
    assert.ok(!REPORT_VIEW_HTML.includes(id),
      `report_view.html が器の DOM（${id}）を書き写しています`);
  }
});

test("the child document boots through the composition root only", () => {
  assert.ok(REPORT_VIEW_HTML.includes("composition_root_front.js"));
  assert.ok(!REPORT_VIEW_HTML.includes("lwc5_chart_renderer.js"),
    "子文書が合成根を飛び越えて部品を掴んでいます");
});

test("the composition root imports the report_ui modules from /sim/report-js/", () => {
  const specs = importSpecifiers(read(ROOT));
  const shared = specs.filter((s) => s.startsWith("/sim/report-js/"));
  assert.deepEqual(shared.sort(), [
    "/sim/report-js/chart.js",
    "/sim/report-js/format.js",
    "/sim/report-js/linkage.js",
    "/sim/report-js/table.js",
  ]);
});

test("the composition root imports nothing but /sim/report-js/ and its own siblings", () => {
  for (const spec of importSpecifiers(read(ROOT))) {
    assert.ok(
      spec.startsWith("/sim/report-js/") || spec.startsWith("./"),
      `合成根が想定外の import を持っています: ${spec}`,
    );
  }
});

test("only the composition root reaches for the shared report_ui modules", () => {
  // F-2/F-3/F-4 は注入で受け取る（＝node:test から素で import できる＝検定可能）。
  for (const name of [VIEW, RENDERER, SOURCE, FRAME]) {
    assert.ok(!read(name).includes("/sim/report-js/"),
      `${name} が移植元を直接 import しています（注入で受け取ること）`);
  }
});

// --- 2. 表示規則の再定義が 0 件 --------------------------------------------------

// 移植元 chart.js / table.js / linkage.js / format.js が所有する定義。sim 側に同名の
// **定義**（const/let/function/class）があれば、それは写しである。
const OWNED_BY_REPORT_UI = [
  "DIM_ALPHA", "MARKER_CAP", "EXIT_COLOR", "DEFAULT_DEPOSIT",
  "balanceForwardFill", "dedupeCurve", "byTimeResolve",
  "buildTradeMarkers", "buildDimBars", "mergeDimBarsForTrade",
  "visibleTradesInRange", "chartBadgeText",
  "createLinkage", "buildTradeTable", "COLS", "compareTrades", "projectRow",
  "fmtMoney", "fmtT", "cfmtLocale", "signClass",
];

test("no sim front module redefines a report_ui symbol (複製 0 の機械強制)", () => {
  const offenders = [];
  for (const name of FRONT_FILES) {
    const src = read(name);
    for (const symbol of OWNED_BY_REPORT_UI) {
      const def = new RegExp(
        `(^|\\n)\\s*(export\\s+)?(const|let|var|function|class)\\s+${symbol}\\b`,
      );
      if (def.test(src)) offenders.push(`${name}: ${symbol}`);
    }
  }
  assert.deepEqual(offenders, []);
});

test("the dim alpha / marker cap literals are not written into the sim layer", () => {
  // 値そのものの写し（0.15 / 700）も禁止する。名前を変えて写せば検定を素通りするため。
  for (const name of FRONT_FILES) {
    const src = read(name);
    assert.ok(!/\b0\.15\b/.test(src), `${name} に減光アルファの実値が書かれています`);
    assert.ok(!/\bMARKER_CAP\s*=\s*\d+/.test(src), `${name} にマーカー上限の実値が書かれています`);
  }
});

test("the trade table columns are not re-declared in the sim layer", () => {
  for (const name of FRONT_FILES) {
    assert.ok(!read(name).includes("Time(close)"),
      `${name} が明細列のラベルを写しています（table.js を import すること）`);
  }
});

// --- 3. v4 vendor への参照が無い（NFR-07）----------------------------------------

test("nothing references the report_ui v4 bundle", () => {
  for (const name of FRONT_FILES) {
    const src = read(name);
    assert.ok(!src.includes("lightweight-charts.standalone.js"), `${name} が v4 バンドルを参照しています`);
    assert.ok(!src.includes("report-vendor"), `${name} が report_ui の vendor 根を参照しています`);
  }
});

// --- 4. lwc に触るのは F-3 だけ（DIP）--------------------------------------------

const LWC_API_NAMES = [
  "createChart", "addSeries", "createSeriesMarkers",
  "CandlestickSeries", "AreaSeries", "BaselineSeries", "CrosshairMode",
];

test("only the v5 adapter mentions lightweight-charts API names", () => {
  for (const name of FRONT_FILES) {
    if (name === RENDERER) continue;
    const src = read(name);
    for (const api of LWC_API_NAMES) {
      assert.ok(!new RegExp(`\\b${api}\\s*\\(`).test(src) && !new RegExp(`lwc\\.${api}\\b`).test(src),
        `${name} が lwc の API（${api}）に触れています（v5 の隔離点は ${RENDERER} だけ）`);
    }
  }
});

test("the v5 adapter does not use any v4 series factory", () => {
  const src = read(RENDERER);
  for (const v4 of ["addCandlestickSeries", "addAreaSeries", "addBaselineSeries", "addLineSeries"]) {
    assert.ok(!src.includes(v4), `${RENDERER} に v4 API（${v4}）が残っています`);
  }
  assert.ok(!/\.setMarkers\s*\(/.test(src.replace(/markerHandle\.setMarkers\s*\(/g, "")),
    "系列へ直接 setMarkers しています（v5 は createSeriesMarkers ハンドル経由）");
});

// --- 5. グローバル document / window を掴まない ----------------------------------

test("view and adapters use the injected doc, not the global document", () => {
  for (const name of [VIEW, RENDERER, SOURCE]) {
    assert.ok(!/\bdocument\./.test(read(name)),
      `${name} がグローバル document を掴んでいます（doc を注入すること）`);
  }
});

// --- 6. E2E フックの所在（移植元 main.js / chart.js と対称）----------------------
// 移植元は「linkage と hover 起動を合成根（main.js:182-183）が」「チャート実体を
// chart.js（:307）が」公開する。sim も同じ配り方にする。合成根はブラウザ絶対パスを
// import するため node:test から実行できない（構造検定で固定する唯一の手段）。

test("the composition root publishes the linkage E2E hooks (main.js:182-183 と対称)", () => {
  const src = read(ROOT);
  assert.ok(src.includes("__simLinkage"), "window.__simLinkage が無い（双方向連動の実測点）");
  assert.ok(src.includes("__simEmitMarkerHover"), "window.__simEmitMarkerHover が無い（マーカー hover の代理）");
});

test("the chart-side E2E hooks live in the v5 adapter only (chart.js:307 と対称)", () => {
  const renderer = read(RENDERER);
  assert.ok(renderer.includes("__simPriceChart") && renderer.includes("__simCandleSeries"),
    `${RENDERER} がチャート実体を公開していません`);
  for (const name of [VIEW, SOURCE, ROOT]) {
    assert.ok(!read(name).includes("__simPriceChart"),
      `${name} がチャート実体を公開しています（実測点の出所は ${RENDERER} 1 箇所）`);
  }
});

test("the composition root reads the job id from the injected location search", () => {
  const src = read(ROOT);
  assert.ok(src.includes("readJobId"), "job_id の読み取りが F-4 の純関数経由ではありません");
  assert.ok(!/["']\?job=/.test(src), "クエリ名を再定義しています（readJobId に閉じること）");
});
