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
// Phase 5 で追加した周辺 View（いずれも純 DOM・lwc に触れない）。
const TABS = "sim_tabs_view.js";
const SEGMENT = "sim_segment_view.js";
const COMPARE = "sim_compare_view.js";
const CONTACTS_TOGGLE = "sim_contacts_toggle_view.js";
const FILTER_PILL = "sim_filter_pill_view.js";
// Phase 6 で追加した実行指示（戦略投入）系（いずれも純 DOM / HTTP・lwc に触れない）。
const SUBMIT_CLIENT = "job_submit_client.js";
const EXEC_PANEL = "sim_execution_panel_view.js";
const EXEC_ROOT = "composition_root_execution.js";
// Phase 8 で追加した Tester Settings（MT5 設定パネル）系（純 DOM / HTTP・lwc に触れない）。
const SETTINGS_CLIENT = "settings_schema_client.js";
const TESTER_PANEL = "sim_tester_settings_panel_view.js";

const WEB_DIR = join(HERE, "..");
const REPORT_VIEW_HTML = readFileSync(join(WEB_DIR, "report_view.html"), "utf8");

/** import 文の from 指定を列挙する。 */
function importSpecifiers(src) {
  return [...src.matchAll(/^\s*import\s[^;]*?from\s+["']([^"']+)["'];/gms)].map((m) => m[1]);
}

// --- 1. 移植元は /sim/report-js/ からだけ引く ------------------------------------

test("the front layer ships exactly the Phase 4 + Phase 5 + Phase 6 + Phase 8 modules", () => {
  assert.deepEqual(FRONT_FILES.sort(), [
    ROOT, RENDERER, SOURCE, VIEW, FRAME,
    TABS, SEGMENT, COMPARE, CONTACTS_TOGGLE, FILTER_PILL,
    SUBMIT_CLIENT, EXEC_PANEL, EXEC_ROOT,
    SETTINGS_CLIENT, TESTER_PANEL,
  ].sort());
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
    // Phase 5 で周辺表示（ヒートマップ・比較判定・用語集）の実体を足す。写さず import する。
    "/sim/report-js/chart.js",
    "/sim/report-js/compare.js",
    "/sim/report-js/data.js",
    "/sim/report-js/format.js",
    "/sim/report-js/glossary.js",
    "/sim/report-js/heatmap.js",
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
  // --- Phase 5 で流用する周辺表示の定義（heatmap / compare / glossary / 接点）---
  // sim 側にこれらの**定義**があれば写しである（import / 注入で受けること）。
  "buildHeatmap", "wdayHourOf", "collectCellIds", "firstTradeInCell", "WEEKORDER", "aggOf",
  "buildCompare", "renderVerdictBanner", "verdictLabel", "parseReportNum", "compareCell",
  "buildCompareRows", "augmentReport", "underwaterCurve", "metricRetention",
  "metricRetentionAll", "degradationBars", "radarClamp", "RADAR_METRICS",
  "buildGlossary", "wireTips", "gkTip", "ggTip",
  "GLOSSARY", "GRAPH_GLOSSARY", "REPORT_GROUPS", "LABELS_JA", "STRATEGY_INFO",
  "contactToMarker", "contactsInRange", "contactsToMarkers",
  "CONTACT_UP_COLOR", "CONTACT_DOWN_COLOR", "CONTACT_MARKER_CAP",
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
    // 接点配色（chart.js:28-29）の実値を写せば、名前を変えても検定を素通りする。hex を封じる。
    assert.ok(!/#f5c542/i.test(src), `${name} に接点 up 配色の実値が書かれています`);
    assert.ok(!/#c084fc/i.test(src), `${name} に接点 down 配色の実値が書かれています`);
    // 接点マーカー id（"c"+idx・chart.js:149）の写しも封じる（contactToMarker を import すること）。
    assert.ok(!/["'`]c["'`]\s*\+/.test(src), `${name} が接点マーカー id（"c"+idx）を写しています`);
  }
});

test("the trade table columns are not re-declared in the sim layer", () => {
  for (const name of FRONT_FILES) {
    assert.ok(!read(name).includes("Time(close)"),
      `${name} が明細列のラベルを写しています（table.js を import すること）`);
  }
});

// --- 2b. Tester Settings の語彙を front が持たない（Phase 8・複製ゼロの機械検査）--------
// 選択肢・キー順・必須キー・非対象理由の単一ソースは `GET /sim/settings-schema` の payload
// （由来は `usecase/tester_settings/enums.py` と検証層・字句層の宣言）である。front に同じ
// 語彙を書いた瞬間、列挙を増やしても UI だけ古いという食い違いが静かに生まれる。
// 時間足ラベル・対象接尾辞の**実値**をソーステキストから機械的に禁じる。

const TESTER_VOCABULARY = [
  // 時間足ラベル（`enums.TIMEFRAME_INI_LABELS` の値。分/時足は M/H＋数字で誤検出しやすい
  // ため、代表として日足以上と M1 を固定する）。
  /\bM1\b/, /\bDaily\b/, /\bWeekly\b/, /\bMonthly\b/,
  // 対象ファイルの接尾辞（`main/tester_settings.SUBJECT_SUFFIX`）。Expert 候補は schema が
  // 連結済みのトークンを配るため、front が接尾辞を知る必要はない。
  /\.ex5\b/,
];

test("no front module writes a Tester Settings vocabulary literal (schema が単一ソース)", () => {
  const offenders = [];
  for (const name of FRONT_FILES) {
    const src = read(name);
    for (const pattern of TESTER_VOCABULARY) {
      if (pattern.test(src)) offenders.push(`${name}: ${pattern}`);
    }
  }
  assert.deepEqual(offenders, []);
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
  "CandlestickSeries", "AreaSeries", "BaselineSeries", "LineSeries", "CrosshairMode",
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
  // setMarkers はマーカーハンドル経由のみ許す。売買（markerHandle）と接点（contactMarkerHandle）の
  //   2 本を除いた残りに `.setMarkers(` があれば、系列へ直接呼んでいる（v5 では系列に無い）。
  const handleCalls = src
    .replace(/markerHandle\.setMarkers\s*\(/g, "")
    .replace(/contactMarkerHandle\.setMarkers\s*\(/g, "");
  assert.ok(!/\.setMarkers\s*\(/.test(handleCalls),
    "系列へ直接 setMarkers しています（v5 は createSeriesMarkers ハンドル経由）");
});

// --- 4b. 実ブラウザ getter 専用プロパティへ代入しない（fake DOM 非検出の穴）-----------
// `Element.children` は実ブラウザで読み取り専用の getter。`el.children = []` は実 UI で
// TypeError（"Cannot set property children ... only a getter"）になり画面が描画されない。
// fake DOM は素の配列プロパティなので代入が通ってしまい単体テストでは露見しない（実測済み）。
// 空にするなら removeChild ループ等を使う。本検定でソーステキストから代入を機械的に禁じる。

test("no front module assigns to the read-only .children getter (実ブラウザ描画事故の防止)", () => {
  const assignChildren = /\.children\s*=(?!=)/; // `=` は許すが `===`/`==` は除外
  for (const name of FRONT_FILES) {
    assert.ok(!assignChildren.test(read(name)),
      `${name} が .children へ代入しています（実ブラウザは getter 専用＝描画事故）。removeChild ループ等で空にすること`);
  }
});

// 非標準 `Element.parent`（読み取り含む）を禁じる。実ブラウザに `.parent` は無く（親参照は
// `parentNode`/`parentElement`）、`node.parent` は常に undefined。fake DOM が非標準 `.parent` を
// 生やしているため単体テストでは通ってしまい、実 UI でのみ「行削除が DOM に反映されない」等の
// 事故になる（🔴-1・fillOptions .children= と同種）。front は parentNode/parentElement を使う。

test("no front module uses the non-standard .parent (parentNode/parentElement を使うこと)", () => {
  const nonStdParent = /\.parent(?!Node|Element)\b/; // .parent は禁止・.parentNode/.parentElement は許可
  for (const name of FRONT_FILES) {
    assert.ok(!nonStdParent.test(read(name)),
      `${name} が非標準の .parent を使っています（実ブラウザは undefined＝親操作が無反応）。parentNode/parentElement を使うこと`);
  }
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

// --- 7. Phase 5 周辺表示の結線（合成根は node:test から実行不可＝構造で固定）----------
// 合成根はブラウザ絶対パス（/sim/report-js/*）を静的 import するため node:test から実行
// できない（本ファイル冒頭の方針・E2E フックと同じ固定手段）。各 View / 純関数の**挙動**は
// それぞれの単体テストが被覆済み（sim_compare_view: canvas 0 件、sim_segment_view: segbtn
// 縮退、sim_contacts_toggle_view: renderer 真実源、sim_filter_pill_view: ピル）。ここでは
// 「合成根がそれらを移植元 main.js:135-190 の順で結線しているか」を構造で固定する。

test("the composition root constructs the four Phase 5 peripheral views", () => {
  const src = read(ROOT);
  for (const factory of [
    "createSimSegmentView", "createSimCompareView",
    "createSimContactsToggleView", "createSimFilterPillView",
  ]) {
    assert.ok(src.includes(factory), `合成根が ${factory} を組み立てていません（結線の欠落）`);
  }
});

test("the composition root imports the peripheral report_ui builders", () => {
  const src = read(ROOT);
  // ヒートマップ・比較判定・用語集の実体は移植元から import（写さない）。
  for (const sym of ["buildHeatmap", "buildCompare", "renderVerdictBanner", "buildGlossary", "wireTips", "aggOf"]) {
    assert.ok(src.includes(sym), `合成根が ${sym} を移植元から引いていません`);
  }
});

test("the composition root wires the extraction filter to the pill (点18)", () => {
  const src = read(ROOT);
  assert.ok(src.includes("subscribeFilter"), "抽出フィルタ購読が結線されていません");
  assert.ok(/filterPill\b/.test(src), "抽出ピル View が結線されていません");
});

test("the composition root renders compare/glossary once at init, segments per run", () => {
  const src = read(ROOT);
  assert.ok(/compareView\b/.test(src), "比較 View が結線されていません");
  assert.ok(/segmentView\b/.test(src), "区間 View が結線されていません");
  assert.ok(/contactsToggle\b/.test(src), "接点トグル View が結線されていません");
  // wireTips は init で 1 回だけ（多重 #tip 禁止）。selectSegment 内に置くと区間切替で増える。
  const wireTipsCount = (src.match(/wireTips\s*\(/g) || []).length;
  assert.equal(wireTipsCount, 1, "wireTips の呼び出しが 1 回ではありません（多重 #tip の恐れ）");
});

test("selectSegment feeds contacts and heatmap (移植元 selectSegment と同順)", () => {
  const src = read(ROOT);
  assert.ok(/setContacts\s*\(/.test(src), "区間切替で接点を renderer へ渡していません");
  assert.ok(/buildHeatmap\s*\(/.test(src), "区間切替でヒートマップを描いていません");
});

test("the single-run heatmap drops the IS/OOS diff view (D-3 の opts を渡す)", () => {
  const src = read(ROOT);
  assert.ok(src.includes("showIsOosDiff"),
    "buildHeatmap へ showIsOosDiff の縮退フラグを渡していません（単一区間で IS/OOS 差を出さない）");
});
