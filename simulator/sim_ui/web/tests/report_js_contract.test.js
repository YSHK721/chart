// 移植元 report_ui が sim へ差し出す**契約**の検定（Phase 5・複製 0 の前提条件）。
//
// なぜ sim 側に置くか: Phase 5 で sim が使う周辺表示（ヒートマップ・比較判定・用語集・
// 接点）は 1 行も写さず `/sim/report-js/` から import する。したがって「その記号が
// export されているか」「単一区間で縮退させる引数があるか」は **sim の可動条件**であり、
// 壊れたら sim が動かなくなる。移植元のテストを書き換えずに（既存ファイルを 1 行も
// 変えずに）契約だけを固定する。
//
// 固定する不変条件:
//   1. R-2: compare.js が判定バナー描画を `renderVerdictBanner` として export する
//      （単一区間では比較グラフを作らず、バナーだけを掲示するため）。
//   2. D-3: heatmap.js の `buildHeatmap` が省略可能引数 opts を受け、`showIsOosDiff:false`
//      で「IS vs OOS 損益差」ビューを出さない（既定＝現行と挙動等価＝5 ビュー）。
//   3. 接点・用語集の純関数／辞書が export されている（sim はこれを import して使う）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildHeatmap } from "../../../report_ui/web/js/heatmap.js";
import { renderVerdictBanner, buildCompare, verdictLabel } from "../../../report_ui/web/js/compare.js";
import { buildGlossary, wireTips, gkTip, ggTip } from "../../../report_ui/web/js/glossary.js";
import {
  contactToMarker, contactsInRange, contactsToMarkers,
  CONTACT_UP_COLOR, CONTACT_DOWN_COLOR, CONTACT_MARKER_CAP,
} from "../../../report_ui/web/js/chart.js";

const IS_OOS_TITLE = "IS vs OOS 損益差";

/** buildHeatmap が書き込む先だけを持つ最小ホスト（DOM 非依存・innerHTML を観測する）。 */
function fakeHost() {
  return {
    innerHTML: "",
    querySelectorAll() { return []; },
  };
}

/** 単一区間（"single"）の最小 payload。heat セルは 1 つで足りる（ビュー数を数えるため）。 */
function singleSegmentData() {
  return {
    segments: {
      single: {
        trades: [{ id: 1, entry_time: 1776643200, profit: 10 }],
        agg: { heat: [{ wday: "Mon", hour: 0, profit: 10, count: 1, wins: 1 }] },
      },
    },
  };
}

const blockCount = (html) => (html.match(/class="heatBlock"/g) || []).length;

// --- 1. R-2: 判定バナーの単体 export -------------------------------------------

test("compare.js exports renderVerdictBanner (単一区間はバナーのみ掲示する)", () => {
  assert.equal(typeof renderVerdictBanner, "function");
  assert.equal(typeof buildCompare, "function");
  assert.equal(verdictLabel("fail"), "過剰最適化");
});

// --- 2. D-3: ヒートマップの区間縮退引数 ----------------------------------------

test("buildHeatmap renders the five views by default (現行と挙動等価)", () => {
  const host = fakeHost();
  buildHeatmap(host, singleSegmentData(), "single", { applyFilter() {} });
  assert.equal(blockCount(host.innerHTML), 5);
  assert.ok(host.innerHTML.includes(IS_OOS_TITLE));
});

test("buildHeatmap drops the IS/OOS diff view when showIsOosDiff is false", () => {
  const host = fakeHost();
  buildHeatmap(host, singleSegmentData(), "single", { applyFilter() {} }, null, {
    showIsOosDiff: false,
  });
  assert.equal(blockCount(host.innerHTML), 4);
  assert.ok(!host.innerHTML.includes(IS_OOS_TITLE));
});

test("buildHeatmap keeps the five views when opts omits the flag (既定は真)", () => {
  const host = fakeHost();
  buildHeatmap(host, singleSegmentData(), "single", { applyFilter() {} }, null, {});
  assert.equal(blockCount(host.innerHTML), 5);
});

// --- 3. sim が import する周辺の純関数・辞書 -----------------------------------

test("glossary.js exports the builders sim mounts in the child document", () => {
  for (const fn of [buildGlossary, wireTips, gkTip, ggTip]) {
    assert.equal(typeof fn, "function");
  }
});

test("chart.js exports the contact pure functions and their palette", () => {
  assert.equal(typeof contactToMarker, "function");
  assert.equal(typeof contactsInRange, "function");
  assert.equal(typeof contactsToMarkers, "function");
  assert.equal(typeof CONTACT_UP_COLOR, "string");
  assert.equal(typeof CONTACT_DOWN_COLOR, "string");
  assert.equal(typeof CONTACT_MARKER_CAP, "number");
});
