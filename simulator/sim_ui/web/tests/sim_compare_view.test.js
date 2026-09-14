// sim_compare_view（比較・判定ペイン・F-7 FR-17）の単体テスト（node:test・fake DOM）。
//
// 固定する不変条件:
//   1. 区間 2 つ以上 → 移植元 buildCompare(payload) を呼ぶ（7 指標カード・劣化表・5 グラフ）。
//   2. 区間 1 つ（sim 実ジョブ）→ renderVerdictBanner(payload) **だけ**を呼ぶ。
//      canvas 0 件・カード 0 件（比較グラフを作らない・P9 縮退）。判定文言は payload が
//      単一ソース——front にハードコードしない（View は payload を渡すだけ）。
//   3. buildCompare / renderVerdictBanner は**注入**で受ける（/sim/report-js/ を直接 import
//      するのは合成根だけ・import_source.test.js が機械強制）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten } from "./_fakes.js";
import { createSimCompareView } from "../js/adapter/front/sim_compare_view.js";

function render(segKeys, spies = {}) {
  const doc = fakeDoc();
  const host = doc.body;
  const calls = { compare: [], verdict: [] };
  const view = createSimCompareView({
    doc,
    buildCompare: (p) => { calls.compare.push(p); if (spies.compare) spies.compare(p); },
    renderVerdictBanner: (p) => { calls.verdict.push(p); },
  });
  const payload = { verdict: { result: "", reasons: ["r1"] }, segments: {} };
  view.render({ host, segKeys, payload });
  return { doc, host, view, calls, payload };
}

const canvases = (host) => flatten(host).filter((n) => n.tagName === "CANVAS");

// --- 1. 2 区間: buildCompare を呼ぶ ---------------------------------------------

test("two segments call buildCompare with the payload", () => {
  const { calls, payload } = render(["is", "oos"]);
  assert.equal(calls.compare.length, 1);
  assert.equal(calls.compare[0], payload);
});

test("two segments do not call renderVerdictBanner directly (buildCompare が内側で呼ぶ)", () => {
  const { calls } = render(["is", "oos"]);
  assert.equal(calls.verdict.length, 0);
});

test("two segments build the comparison canvases (5 グラフの受け皿)", () => {
  const { host } = render(["is", "oos"]);
  const ids = canvases(host).map((c) => c.id);
  for (const id of ["cmpEquity", "cmpPnl", "cmpDD", "cmpRadar", "cmpDeg"]) {
    assert.ok(ids.includes(id), `canvas #${id} が無い`);
  }
});

// --- 2. 単一区間: 判定バナーだけ（縮退）----------------------------------------

test("a single segment calls renderVerdictBanner with the payload", () => {
  const { calls, payload } = render(["single"]);
  assert.equal(calls.verdict.length, 1);
  assert.equal(calls.verdict[0], payload);
});

test("a single segment does not call buildCompare", () => {
  const { calls } = render(["single"]);
  assert.equal(calls.compare.length, 0);
});

test("a single segment builds no canvas (canvas 0 件・比較グラフを作らない)", () => {
  const { host } = render(["single"]);
  assert.equal(canvases(host).length, 0);
});

test("a single segment builds no metric cards (カード 0 件)", () => {
  const { host } = render(["single"]);
  assert.equal(findById(host, "cmpBasic"), null);
  assert.equal(findById(host, "cmpTable"), null);
});

// --- 判定バナーの受け皿は両モードで存在する ------------------------------------

test("the verdict host (#cmpVerdict) exists in both modes", () => {
  assert.ok(findById(render(["single"]).host, "cmpVerdict"));
  assert.ok(findById(render(["is", "oos"]).host, "cmpVerdict"));
});

// --- 3. 文言は front にハードコードしない --------------------------------------

test("the view injects no verdict wording of its own (payload が単一ソース)", () => {
  // View は payload を渡すだけ。判定語（過剰最適化/要注意/合格）を自前で書かない。
  const src = require_source();
  for (const word of ["過剰最適化", "要注意", "合格"]) {
    assert.ok(!src.includes(word), `View が判定文言「${word}」を写しています`);
  }
});

test("the view uses the injected doc only (no global document)", () => {
  const { host } = render(["single"]);
  assert.ok(flatten(host).every((el) => typeof el.appendChild === "function"));
});

// 本ファイルは ESM だが、View ソースの文言写し検査のためだけに同期読みする。
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
function require_source() {
  const here = dirname(fileURLToPath(import.meta.url));
  return readFileSync(join(here, "..", "js", "adapter", "front", "sim_compare_view.js"), "utf8");
}
