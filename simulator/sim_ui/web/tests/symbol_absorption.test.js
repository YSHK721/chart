// 銘柄による実行対象データセットの決定（Phase 9 S4・§19.2「吸収」）。
//
// データセット選択という sim 独自の概念を画面から落とし、MT5 が持つ概念（Symbol）だけを残す。
// 実行対象データセットは Symbol から**決定的に**引く（resolveProfile＝一致の先頭）。
//
// なぜ「解決できたときだけ」書き戻すか: 利用者が打った銘柄を UI が勝手に別の値へ戻すと、
// 「入れたはずの値が消える」画面になる（ビュー自動介入の禁止）。解決できない銘柄は
// そのまま残し、直前の profile を保ったうえで**警告を点ける**——不一致のまま投入すれば
// 実行時に失敗することは、投入前に画面へ出ていなければならない。
//
// 固定する不変条件:
//   1. 銘柄は候補付き select になる（候補は run-options の datasets 由来）。
//   2. 候補 0 件なら自由入力へ縮退する（候補が無いことを理由に投入不能にしない）。
//   3. 系列の軸は**実在する分岐のときだけ**画面に出る（ISSUE-511 段階 8-D-5・設計 D-3）。
//   4. 解決できる銘柄を選ぶと profile が切り替わる。
//   5. 解決できない銘柄を選んでも欄は書き戻らず、警告が点く。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { fakeDoc, findById, flatten, IDLE_WATCH_TIMER } from "./_fakes.js";
import { settingsSchema } from "./_settings_schema_fixture.js";
import { createSimTesterSettingsPanelView } from "../js/adapter/front/sim_tester_settings_panel_view.js";
import { createSimSchemaFallbackView } from "../js/adapter/front/sim_schema_fallback_view.js";
import { mountSimExecutionPanel } from "../js/adapter/front/composition_root_execution.js";

const flush = () => new Promise((r) => setTimeout(r, 0));
const fire = (el, ev = "change") => (el._listeners[ev] || []).forEach((f) => f());

/** 銘柄の異なる 2 つのデータセット（`period` は schema fixture のトークンに揃える）。 */
const DATASETS = [
  {
    dataset: "ds_a", data_path: "/d/a.csv", symbol: "AAA", period: "P2",
    contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
    volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: 0,
    settlement_currency: "XYZ",
  },
  {
    dataset: "ds_b", data_path: "/d/b.csv", symbol: "BBB", period: "P2",
    contract_size: 1.0, digits: 3, point_size: 0.001, leverage: 20.0,
    volume_min: 0.1, volume_max: 10.0, volume_step: 0.1, stops_level: 5,
    settlement_currency: "XYZ",
  },
];
const EA_NAMES = ["PRO_fit_Band_EA", "TC24051901"];

/** 同一銘柄の 2 系列（段階 8-D-3 のカタログと同形: 同じ `symbol` で `dataset` だけが違う）。
 *  ref 名は front が持ち得ない綴りにする——ラベル・値が run-options 由来でなければ
 *  この綴りは画面に出せない（G3 の実証）。`stops_level` で系列の別が本文に現れる。 */
const SERIES_DATASETS = [
  { ...DATASETS[0], dataset: "zzz_alpha", data_path: "/d/alpha.csv" },
  { ...DATASETS[0], dataset: "zzz_beta", data_path: "/d/beta.csv", stops_level: 7 },
];
/** 系列 1 本だけの構成（SERIES_DATASETS の先頭のみ）。 */
const SINGLE_SERIES = [SERIES_DATASETS[0]];
const SERIES_REFS = SERIES_DATASETS.map((d) => d.dataset);

function routerFetch({ schema, datasets = DATASETS } = {}) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    if (url === "/sim/settings-schema") {
      return schema
        ? { ok: true, status: 200, json: async () => schema }
        : { ok: false, status: 404, json: async () => ({ error: "no schema" }) };
    }
    if (url === "/sim/run-options") {
      return { ok: true, status: 200, json: async () => ({ ok: true, datasets, ea_names: EA_NAMES }) };
    }
    if (url === "/sim/jobs") return { ok: true, status: 202, json: async () => ({ job_id: "j1", status: "running" }) };
    return { ok: false, status: 404, json: async () => ({ error: "nope" }) };
  };
  fn.calls = calls;
  return fn;
}

async function mountRoot(opts = {}) {
  const doc = fakeDoc();
  const fetchFn = routerFetch(opts);
  const warn = console.warn;
  console.warn = () => {};
  try {
    await mountSimExecutionPanel({ ...IDLE_WATCH_TIMER, doc, host: doc.body, fetch: fetchFn });
  } finally {
    console.warn = warn;
  }
  return { doc, host: doc.body, fetchFn };
}

/** 投入して本文を読む。 */
async function submitBody(host, fetchFn) {
  findById(host, "runStart")._listeners.click[0]();
  await flush();
  return JSON.parse(fetchFn.calls.find((c) => c.url === "/sim/jobs").init.body);
}

// --- 1/2. 銘柄は候補付き select・候補 0 件で自由入力へ縮退 ------------------------

test("the tester panel renders Symbol as a select over the injected candidates", () => {
  const doc = fakeDoc();
  const view = createSimTesterSettingsPanelView({ doc });
  view.mount(doc.body);
  view.setSymbolCandidates(["AAA", "BBB"]);
  view.setSchema(settingsSchema());
  const node = findById(doc.body, "testerSymbol");
  assert.equal(node.tagName, "SELECT");
  assert.deepEqual((node.children || []).map((o) => o.value), ["AAA", "BBB"]);
  assert.equal(view.selectedSymbol(), "AAA");
});

test("the tester panel degrades Symbol to free input when no candidate exists", () => {
  const doc = fakeDoc();
  const view = createSimTesterSettingsPanelView({ doc });
  view.mount(doc.body);
  view.setSymbolCandidates([]);
  view.setSchema(settingsSchema());
  assert.equal(findById(doc.body, "testerSymbol").tagName, "INPUT");
});

test("the fallback surface renders Symbol as a select over the injected candidates", () => {
  const doc = fakeDoc();
  const view = createSimSchemaFallbackView({ doc });
  view.setSymbolCandidates(["AAA", "BBB"]);
  view.mount(doc.body);
  const node = findById(doc.body, "execSymbol");
  assert.equal(node.tagName, "SELECT");
  assert.deepEqual((node.children || []).map((o) => o.value), ["AAA", "BBB"]);
  assert.equal(view.selectedSymbol(), "AAA");
});

test("the fallback surface degrades Symbol to free input when no candidate exists", () => {
  const doc = fakeDoc();
  const view = createSimSchemaFallbackView({ doc });
  view.mount(doc.body);
  assert.equal(findById(doc.body, "execSymbol").tagName, "INPUT");
  assert.equal(view.selectedSymbol(), "");
});

test("the fallback surface reports a symbol change to its subscriber", () => {
  const doc = fakeDoc();
  const view = createSimSchemaFallbackView({ doc });
  view.setSymbolCandidates(["AAA", "BBB"]);
  view.mount(doc.body);
  const seen = [];
  view.onSymbolChange((s) => seen.push(s));
  const node = findById(doc.body, "execSymbol");
  node.value = "BBB";
  fire(node);
  assert.deepEqual(seen, ["BBB"]);
});

// --- 3. 系列の軸は実在する分岐のときだけ出る（ISSUE-511 段階 8-D-5・設計 D-3）--------
//
// **置き換えの記録**: S4 は「データセット選択という sim 独自の概念を画面から落とす」ため、
// 選択 UI の**不在**を固定していた（`execDataset` が 0 件であること）。しかしカタログは
// 同一銘柄で 2 本の系列を返すようになった（段階 8-D-3）。不在を固定し続けると 2 本目は
// **到達不能**であり、「提供していると名乗るのに選べない」状態が検定に守られてしまう。
//
// したがって固定するものを「概念を出さない」から「**実在する分岐のときだけ出す**」へ
// 精緻化する。認知負荷の最小化はこれで保たれる——分岐が無ければ画面は 1 つも増えない。
// 旧 `execDataset`（銘柄と無関係なデータセット一覧＝sim 独自の概念）は復活させない。
//
//   G1: 系列が 2 件以上あるときだけ選択 UI を出す（1 件なら現行画面と同一）。
//   G2: 系列の軸は投入本文に寄与しない（PROFILE_KEYS 不変・本文の parity が緑）。
//   G3: ラベル・値は run-options 由来（front にリテラルを持たせない）。

/** 面ごとの観測点（settings 構成 = M1 / 縮退構成 = M4）。 */
const SURFACES = [
  ["settings 構成", "testerSeries", (datasets) => mountRoot({ schema: settingsSchema(), datasets })],
  ["縮退構成", "execSeries", (datasets) => mountRoot({ datasets })],
];

// --- G1: 実在する分岐のときだけ出す ------------------------------------------------

for (const [label, seriesId, mount] of SURFACES) {
  test(`${label}: two series of one symbol ship a series selector (G1)`, async () => {
    const { host } = await mount(SERIES_DATASETS);
    const node = findById(host, seriesId);
    assert.ok(node, "系列が 2 件あるのに選択 UI が出ていません（2 本目が到達不能）");
    assert.equal(node.tagName, "SELECT");
    assert.deepEqual((node.children || []).map((o) => o.value), SERIES_REFS);
  });

  test(`${label}: a single series ships no series selector at all (G1・現行画面と同一)`, async () => {
    const { host } = await mount(SINGLE_SERIES);
    // 不在の主張は**真偽で**書く（要素と null を assert.equal に渡すと、fake DOM の
    // 循環参照つき部分木の差分生成に十数秒かかる＝失敗時に検定が使えなくなる・実測）。
    assert.ok(findById(host, seriesId) === null,
      "系列が 1 件しか無いのに選択 UI が出ています（実在しない分岐を画面に出しています）");
    // 旧 sim 独自の「データセット選択」は、系列が何本あっても復活させない。
    assert.ok(findById(host, "execDataset") === null, "データセット選択が復活しています");
  });

  test(`${label}: two symbols of one series each ship no series selector (G1)`, async () => {
    // 銘柄が 2 つでも、各銘柄の系列が 1 本なら軸は出ない（軸は銘柄の**内側**にある）。
    const { host } = await mount(DATASETS);
    assert.ok(findById(host, seriesId) === null, "銘柄の軸を系列の軸と取り違えています");
    assert.ok(findById(host, "execDataset") === null, "データセット選択が復活しています");
  });
}

// --- 2. 選んだ系列に応じて profile が解決される（先頭決め打ちでない）-----------------

test("settings 構成: the resolved profile follows the chosen series", async () => {
  const { host, fetchFn } = await mountRoot({ schema: settingsSchema(), datasets: SERIES_DATASETS });
  const series = findById(host, "testerSeries");
  assert.equal(series.value, SERIES_REFS[0], "初期選択が run-options の先頭ではありません");
  series.value = SERIES_REFS[1];
  fire(series);
  const body = await submitBody(host, fetchFn);
  assert.equal(body.backtest.data_path, "/d/beta.csv", "選んだ系列の実体が使われていません");
  assert.equal(body.backtest.stops_level, 7, "選んだ系列の銘柄仕様が使われていません");
});

test("縮退構成: the resolved profile follows the chosen series", async () => {
  const { host, fetchFn } = await mountRoot({ datasets: SERIES_DATASETS });
  const series = findById(host, "execSeries");
  series.value = SERIES_REFS[1];
  fire(series);
  const body = await submitBody(host, fetchFn);
  assert.equal(body.backtest.data_path, "/d/beta.csv");
  assert.equal(body.backtest.stops_level, 7);
});

test("settings 構成: not choosing a series runs the first one (既定は run-options の先頭)", async () => {
  const { host, fetchFn } = await mountRoot({ schema: settingsSchema(), datasets: SERIES_DATASETS });
  const body = await submitBody(host, fetchFn);
  assert.equal(body.backtest.data_path, "/d/alpha.csv");
  assert.equal(body.backtest.stops_level, 0);
});

// --- G2: 系列の軸は投入本文に寄与しない -------------------------------------------

/** 入れ子ごとキーをソートする（並び順を比較対象から外す＝正規化）。 */
function sortDeep(v) {
  if (Array.isArray(v)) return v.map(sortDeep);
  if (v && typeof v === "object") {
    return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortDeep(v[k])]));
  }
  return v;
}

for (const [label, , mount] of SURFACES) {
  test(`${label}: adding the series axis changes the submitted body by 0 byte (G2)`, async () => {
    // 軸の在る構成（2 系列）と無い構成（1 系列）で、既定のまま投入した本文を突き合わせる。
    // 先頭系列は同一なので、差が出たら軸そのものが本文へ漏れている。
    const single = await mount(SINGLE_SERIES);
    const withAxis = await mount(SERIES_DATASETS);
    const a = JSON.stringify(sortDeep(await submitBody(single.host, single.fetchFn)));
    const b = JSON.stringify(sortDeep(await submitBody(withAxis.host, withAxis.fetchFn)));
    assert.equal(b, a, "系列の軸を出しただけで投入本文が変わりました");
  });

  test(`${label}: choosing another series adds no key to the submitted body (G2)`, async () => {
    const first = await mount(SERIES_DATASETS);
    const bodyFirst = await submitBody(first.host, first.fetchFn);
    const second = await mount(SERIES_DATASETS);
    const node = flatten(second.host).find((n) => n.dataset && n.dataset.mt5 === "ui:series");
    assert.ok(node, "系列の軸が `ui:` 宣言を持っていません（本文に寄与しない宣言が必要）");
    node.value = SERIES_REFS[1];
    fire(node);
    const bodySecond = await submitBody(second.host, second.fetchFn);
    assert.deepEqual(Object.keys(bodySecond.backtest).sort(), Object.keys(bodyFirst.backtest).sort());
    assert.deepEqual(
      Object.keys((bodySecond.settings && bodySecond.settings.tester) || {}).sort(),
      Object.keys((bodyFirst.settings && bodyFirst.settings.tester) || {}).sort(),
    );
    assert.equal("dataset" in bodySecond.backtest, false, "系列の識別子が本文に載っています");
    assert.equal("series" in bodySecond, false, "系列のブロックが本文に載っています");
  });
}

// --- G3: ラベル・値は run-options 由来（front リテラル 0）---------------------------

for (const [label, seriesId, mount] of SURFACES) {
  test(`${label}: every series option is labelled by the injected run-options (G3)`, async () => {
    const { host } = await mount(SERIES_DATASETS);
    const options = findById(host, seriesId).children || [];
    assert.deepEqual(options.map((o) => o.value), SERIES_REFS);
    assert.deepEqual(options.map((o) => String(o.textContent)), SERIES_REFS,
      "系列のラベルが run-options 由来ではありません（front が表示名を作っています）");
  });
}

const FRONT_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "js", "adapter", "front");
/** 実運用の系列 ref の綴り（`marketdata` の台帳と `SymbolSpecCatalog` が唯一の権威）。 */
const SERIES_REF_LITERAL = /jp225/i;

test("the series-literal detector actually sees a written ref (自己検定)", () => {
  // 変異 1 点: front が系列の綴りを持ち込んだ状態（実際に起きうる複製の形）。
  assert.ok(SERIES_REF_LITERAL.test('const SERIES = ["jp225_m1"];'),
    "検出器が系列 ref の写しを見逃しています（下の走査は無意味）");
  assert.ok(!SERIES_REF_LITERAL.test("const refs = seriesCandidatesOf(datasets, symbol);"));
});

test("no front module writes a series ref literal (G3・単一ソースは run-options)", () => {
  const files = readdirSync(FRONT_DIR).filter((f) => f.endsWith(".js"));
  assert.ok(files.length > 0, "front モジュールを 1 本も収集できていません（この走査は空振りです）");
  const offenders = files.filter(
    (name) => SERIES_REF_LITERAL.test(readFileSync(join(FRONT_DIR, name), "utf8")));
  assert.deepEqual(offenders, [],
    "front が系列 ref の綴りを持っています（ラベル・値は run-options からのみ来ること）");
});

// --- 5. 縮退面でも壊れない（schema 取得失敗）---------------------------------------

test("the degraded surface keeps every panel while the series axis is shown", async () => {
  const { host } = await mountRoot({ datasets: SERIES_DATASETS });
  for (const id of ["simSchemaFallbackPanel", "simEaInputsPanel", "simRunActionPanel"]) {
    assert.ok(findById(host, id), `${id} が描かれていません`);
  }
  // 銘柄の欄は従来どおり在り、系列はその隣に足されただけである。
  assert.equal(findById(host, "execSymbol").tagName, "SELECT");
  assert.ok(findById(host, "execSeries"), "縮退面に系列の軸が出ていません");
});

test("both subject sources implement the series axis members (Port の対称性)", () => {
  const doc = fakeDoc();
  const tester = createSimTesterSettingsPanelView({ doc });
  tester.mount(doc.body);
  const fallback = createSimSchemaFallbackView({ doc });
  fallback.mount(doc.body);
  for (const [name, view] of [["tester settings panel", tester], ["schema fallback", fallback]]) {
    for (const member of ["setSeriesCandidates", "selectedSeries", "onSeriesChange"]) {
      assert.equal(typeof view[member], "function", `${name}: Port の ${member} が無い`);
    }
    assert.equal(view.selectedSeries(), "", `${name}: 未確定の系列が文字列で返りません`);
  }
});

// --- 4. 銘柄から実行対象データセットが決まる -------------------------------------

test("the resolved profile follows the chosen Symbol (settings 構成)", async () => {
  const { host, fetchFn } = await mountRoot({ schema: settingsSchema() });
  const symbol = findById(host, "testerSymbol");
  assert.equal(symbol.value, "AAA");
  symbol.value = "BBB";
  fire(symbol);
  const body = await submitBody(host, fetchFn);
  assert.equal(body.backtest.symbol, "BBB");
  assert.equal(body.backtest.data_path, "/d/b.csv");
  assert.equal(body.backtest.contract_size, 1.0);
  assert.equal(body.backtest.stops_level, 5);
});

test("the resolved profile follows the chosen Symbol (縮退構成)", async () => {
  const { host, fetchFn } = await mountRoot();
  const symbol = findById(host, "execSymbol");
  symbol.value = "BBB";
  fire(symbol);
  const body = await submitBody(host, fetchFn);
  assert.equal(body.backtest.symbol, "BBB");
  assert.equal(body.backtest.data_path, "/d/b.csv");
});

// --- 5. 解決できない銘柄は書き戻さず警告を点ける ---------------------------------

test("an unresolvable Symbol is left in the field and lights the mismatch warning", async () => {
  const { host } = await mountRoot({ schema: settingsSchema() });
  const symbol = findById(host, "testerSymbol");
  symbol.value = "NOPE";           // 候補外（実 UI では自由入力の縮退時に起こる）
  fire(symbol);
  // 欄は書き戻らない（利用者の入力を UI が勝手に戻さない）
  assert.equal(symbol.value, "NOPE", "利用者の入力が書き戻されました");
  // 警告が点く（不一致のまま投入すれば実行時に失敗することを投入前に出す）
  assert.ok(findById(host, "simTesterWarn").textContent.includes("NOPE"),
    "不一致なのに警告が出ていません");
});

test("an unresolvable Symbol keeps the previously resolved profile (直前の profile を保つ)", async () => {
  const { host, fetchFn } = await mountRoot({ schema: settingsSchema() });
  const symbol = findById(host, "testerSymbol");
  symbol.value = "BBB";
  fire(symbol);
  symbol.value = "NOPE";
  fire(symbol);
  const body = await submitBody(host, fetchFn);
  // 直前に解決できた BBB の profile がそのまま使われる（既定へ勝手に戻さない）
  assert.equal(body.backtest.data_path, "/d/b.csv");
  // 投入本文の Symbol は利用者が打った値のまま（黙って別の銘柄で回さない）
  assert.equal(body.settings.tester.Symbol, "NOPE");
});
