// 系列の軸（ISSUE-511 段階 8-D-5）の計算量テスト（絶対命令 2026-08-28・`tdd` スキル §4.1）。
//
// 何を測るか: **時間ではなく回数**。ここで数えるのは
//   (a) 画面が発行した HTTP 要求の回数（Test Spy は注入 fetch に張る）
//   (b) 画面が生成した DOM 要素の個数（Test Spy は注入 doc.createElement に張る）
//   である。どちらも front の当該計算が唯一通る継ぎ目であり、注入で素のまま数えられる。
//
// 何を固定するか: **無駄の不在**であって実装詳細ではない。
//   `発行した要素 − 出力に使った要素 = 0`。回数そのもの（「N 個作られること」）は焼き込まない
//   ——固定すると浪費が仕様へ昇格する（ISSUE-450 の実例）。使った数は**出力から数える**
//   （画面に載った select と option の本数）ため、実装が何個作ろうと期待値は動かない。
//
// なぜ状態検証では足りないか: 系列の軸は「候補を注入し直すたびにフォーム全体を作り直す」
//   実装でも出力（選べる系列・解決される profile・投入本文）が完全に正しいままである。
//   したがって `symbol_absorption.test.js` を 1 件も赤にしない。にもかかわらず、
//   作り直しは (1) 入力中の値を初期値へ戻し (2) 直前に利用者が触った要素を差し替える
//   （ビュー自動介入の禁止に触れる）。捨てられる生成を数えることでしか落ちない。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, IDLE_WATCH_TIMER } from "./_fakes.js";
import { settingsSchema } from "./_settings_schema_fixture.js";
import { createSimTesterSettingsPanelView } from "../js/adapter/front/sim_tester_settings_panel_view.js";
import { createSimSchemaFallbackView } from "../js/adapter/front/sim_schema_fallback_view.js";
import { mountSimExecutionPanel } from "../js/adapter/front/composition_root_execution.js";

const flush = () => new Promise((r) => setTimeout(r, 0));
const fire = (el, ev = "change") => (el._listeners[ev] || []).forEach((f) => f());

/** 同一銘柄の系列を n 本持つ datasets（入力量を変える 2 点目のため）。 */
function datasetsOfSize(n) {
  return Array.from({ length: n }, (_, i) => ({
    dataset: `zzz_s${i}`, data_path: `/d/s${i}.csv`, symbol: "AAA", period: "P2",
    contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
    volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: i,
    settlement_currency: "XYZ",
  }));
}

/** Test Spy を張った document ダブル（生成した要素の個数を数える）。 */
function countingDoc() {
  const doc = fakeDoc();
  const real = doc.createElement;
  doc.created = 0;
  doc.createElement = (tag) => { doc.created += 1; return real(tag); };
  return doc;
}

/** `fn` の実行中に発行された生成の個数を数える。 */
function issuedElements(doc, fn) {
  const before = doc.created;
  const value = fn();
  return { value, issued: doc.created - before };
}

/** 出力が実際に使った要素の個数（期待値はここから採る＝個数を焼き込まない）。
 *  系列の軸の出力は「select 1 個 ＋ その option の本数」である。 */
function elementsUsedBy(node) {
  return node ? 1 + (node.children || []).length : 0;
}

function routerFetch({ schema, datasets }) {
  const calls = [];
  const fn = async (url) => {
    calls.push(url);
    if (url === "/sim/settings-schema") {
      return schema
        ? { ok: true, status: 200, json: async () => schema }
        : { ok: false, status: 404, json: async () => ({ error: "no schema" }) };
    }
    if (url === "/sim/run-options") {
      return { ok: true, status: 200, json: async () => ({ ok: true, datasets, ea_names: ["TC24051901"] }) };
    }
    if (url === "/sim/jobs") return { ok: true, status: 202, json: async () => ({ job_id: "j1", status: "running" }) };
    return { ok: false, status: 404, json: async () => ({ error: "nope" }) };
  };
  fn.calls = calls;
  return fn;
}

async function mountRoot({ schema = settingsSchema(), datasets = datasetsOfSize(2) } = {}) {
  const doc = countingDoc();
  const fetchFn = routerFetch({ schema, datasets });
  const warn = console.warn;
  console.warn = () => {};
  try {
    await mountSimExecutionPanel({ ...IDLE_WATCH_TIMER, doc, host: doc.body, fetch: fetchFn });
  } finally {
    console.warn = warn;
  }
  return { doc, host: doc.body, fetchFn };
}

/** 面（View）だけを素で立てる（合成根も通信も通さない）。 */
function testerView(doc) {
  const view = createSimTesterSettingsPanelView({ doc });
  view.mount(doc.body);
  view.setSchema(settingsSchema());
  return view;
}
function fallbackView(doc) {
  const view = createSimSchemaFallbackView({ doc });
  view.mount(doc.body);
  return view;
}

const SURFACES = [
  ["settings 構成", "testerSeries", testerView],
  ["縮退構成", "execSeries", fallbackView],
];

// --- 0. 検出器の自己検定（空振りする spy で下の主張をしない）------------------------

test("the element spy actually counts a creation (自己検定)", () => {
  const doc = countingDoc();
  const { issued } = issuedElements(doc, () => doc.createElement("div"));
  assert.equal(issued, 1, "生成を数えられていません（下の走査は無意味です）");
});

// --- 1. 無駄の不在: 候補の注入が捨てる生成を持たない -------------------------------

for (const [label, seriesId, make] of SURFACES) {
  test(`${label}: injecting series candidates creates nothing it does not show`, () => {
    const doc = countingDoc();
    const view = make(doc);
    const { issued } = issuedElements(doc, () => view.setSeriesCandidates(["zzz_s0", "zzz_s1"]));
    const used = elementsUsedBy(findById(doc.body, seriesId));
    assert.ok(used > 0, "系列の軸が出ていません（前提が崩れています）");
    assert.equal(issued - used, 0,
      `捨てられる生成があります: 発行 ${issued} / 出力に使った ${used}`);
  });

  test(`${label}: re-injecting the same candidates creates nothing at all`, () => {
    // 同じ候補を配り直すのは出力を 1 ビットも変えない＝生成はすべて無駄である。
    const doc = countingDoc();
    const view = make(doc);
    view.setSeriesCandidates(["zzz_s0", "zzz_s1"]);
    const before = findById(doc.body, seriesId);
    const { issued } = issuedElements(doc, () => view.setSeriesCandidates(["zzz_s0", "zzz_s1"]));
    assert.equal(issued, 0, "同じ候補の注入で作り直しています（出力は変わらないのに生成しています）");
    // 同一性の主張も真偽で書く（差分生成の爆発を避ける・上の理由と同じ）。
    assert.ok(findById(doc.body, seriesId) === before, "同じ候補で要素が差し替わりました");
  });

  test(`${label}: injecting series candidates does not rebuild the rest of the form`, () => {
    // 作り直しは出力を変えないため状態検証では落ちない。入力中の値を初期値へ戻し、
    // 直前に触った要素を差し替える（ビュー自動介入の禁止）ため、ここで落とす。
    const doc = countingDoc();
    const view = make(doc);
    view.setSymbolCandidates(["AAA"]);
    const symbolId = label === "settings 構成" ? "testerSymbol" : "execSymbol";
    const symbolBefore = findById(doc.body, symbolId);
    view.setSeriesCandidates(["zzz_s0", "zzz_s1"]);
    assert.ok(findById(doc.body, symbolId) === symbolBefore,
      "系列候補の注入で銘柄の欄が作り直されました（フォーム全体を捨てて組み直しています）");
  });
}

// --- 2. オーダーの表明: 発行は出力量だけで決まる -----------------------------------

for (const [label, seriesId, make] of SURFACES) {
  test(`${label}: the issued count follows the number of options, nothing else`, () => {
    // 2 点で固定する（2 系列 / 5 系列）。差は option の増分ちょうどであり、
    // 期待値は**出力から**採る（絶対個数を焼き込まない）。
    const points = [2, 5].map((n) => {
      const doc = countingDoc();
      const view = make(doc);
      const refs = datasetsOfSize(n).map((d) => d.dataset);
      const { issued } = issuedElements(doc, () => view.setSeriesCandidates(refs));
      return { issued, used: elementsUsedBy(findById(doc.body, seriesId)) };
    });
    assert.equal(points[1].issued - points[0].issued, points[1].used - points[0].used,
      "系列を増やすと出力に無い生成が増えています（候補数に比例する無駄があります）");
    for (const point of points) assert.equal(point.issued - point.used, 0);
  });
}

// --- 3. 画面から投入までの経路: 系列の切替は取得を発行しない -----------------------

test("switching the series issues no request of its own (取得は mount の 2 本だけ)", async () => {
  const { host, fetchFn } = await mountRoot();
  const before = fetchFn.calls.length;
  const series = findById(host, "testerSeries");
  assert.ok(series, "系列の軸が出ていません（前提が崩れています）");
  series.value = "zzz_s1";
  fire(series);
  await flush();
  assert.equal(fetchFn.calls.length - before, 0,
    `系列の切替で取得を発行しています: ${fetchFn.calls.slice(before).join(",")}`);
});

test("switching the series creates no DOM element (フォームを作り直さない)", async () => {
  const { doc, host } = await mountRoot();
  const series = findById(host, "testerSeries");
  const symbolBefore = findById(host, "testerSymbol");
  const { issued } = issuedElements(doc, () => { series.value = "zzz_s1"; fire(series); });
  assert.equal(issued, 0, "系列の切替でフォームを作り直しています（作って捨てる生成）");
  assert.ok(findById(host, "testerSymbol") === symbolBefore, "銘柄の欄が差し替わりました");
  assert.ok(findById(host, "testerSeries") === series, "系列の欄が自分を差し替えました");
});

test("mounting a single-series configuration issues no series element at all", async () => {
  // 分岐が実在しないときは軸の生成そのものが 0（出さない UI に費用を払わない）。
  const twoSeries = await mountRoot({ datasets: datasetsOfSize(2) });
  const oneSeries = await mountRoot({ datasets: datasetsOfSize(1) });
  const used = elementsUsedBy(findById(twoSeries.host, "testerSeries"));
  assert.ok(used > 0, "2 系列で軸が出ていません（前提が崩れています）");
  assert.ok(findById(oneSeries.host, "testerSeries") === null,
    "系列 1 本の構成で軸が出ています");
  assert.equal(twoSeries.doc.created - oneSeries.doc.created, used,
    "軸の在無で生成数の差が出力量と一致しません（出さない UI を作って捨てています）");
});
