// 投入本文の不変性ゲート（Phase 9 S2 以降・§19.3 の通過条件「S1 直後と byte 一致」）。
//
// なぜ検定にするか: S2〜S6 は front の**面の切り方**を変える改修であり、投入本文は 1 bit も
// 変えてはならない。面を割るたびに「たぶん同じ」と言い続けると、キーの取りこぼし・型の
// 落ち方（`Math.trunc` の有無・`String(10)` と `str(10.0)` の食い違い）が静かに入り込む。
// 本番の合成根を fake DOM で動かして得た本文を、**S1 直後に実測した golden** と突き合わせる。
//
// golden は正規化（キー再帰ソート）した JSON である。キーの並び順は本文の意味ではない
// （サーバは Mapping として読む）。比較しているのは**キー集合と値と型**である。
//
// 固定する不変条件:
//   1. settings 構成（schema あり）の投入本文が golden と一致する。
//   2. legacy 構成（schema 取得失敗＝縮退面が権威）の投入本文が golden と一致する。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById } from "./_fakes.js";
import { settingsSchema } from "./_settings_schema_fixture.js";
import { mountSimExecutionPanel } from "../js/adapter/front/composition_root_execution.js";

/** 実行条件（データセット 1 件）。`period` は schema fixture の `Period` トークンに揃える
 *  （揃えないと不一致警告の分岐に入り、比較したい本文以外の差が混ざる）。 */
const RUN_OPTIONS = {
  ok: true,
  datasets: [{
    dataset: "jp225_m1", data_path: "/d/jp225_m1.csv", symbol: "JP225", period: "P2",
    contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
    volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: 0,
    settlement_currency: "XYZ",
  }],
  ea_names: ["PRO_fit_Band_EA", "TC24051901"],
};

/** 同一操作（S1 直後の実測時と同じ入力）。 */
const OPERATION = Object.freeze({
  stop_loss_points: "100", take_profit_points: "200",
  ma_period: "30", ma_method: "sma", lot_size: "0.5",
  initial_deposit: "10000000",   // legacy 構成でのみ入力する
});

/** S1 直後（f3085e7）に本番合成根を動かして実測した投入本文（正規化済み）。 */
const GOLDEN = {
  settings: {
    backtest: {
      contract_size: 10, data_path: "/d/jp225_m1.csv", digits: 1,
      ea_name: "PRO_fit_Band_EA", initial_deposit: 10000, leverage: 10,
      lot_size: 0.5, ma_method: "sma", ma_period: 30, period: "P2",
      point_size: 0.1, stop_loss_points: 100, stops_level: 0, symbol: "JP225",
      take_profit_points: 200, volume_max: 100, volume_min: 0.01, volume_step: 0.01,
    },
    settings: {
      inputs: [],
      tester: {
        Currency: "XYZ", Dates: "d0", Deposit: "10000", ExecutionMode: "7",
        Expert: "PRO_fit_Band_EA.zzz", ForwardMode: "f0", Leverage: "10", Model: "m0",
        Optimization: "o0", OptimizationCriterion: "c0", Period: "P2",
        ProfitInPips: "0", Symbol: "JP225", Visual: "0",
      },
    },
  },
  legacy: {
    backtest: {
      contract_size: 10, data_path: "/d/jp225_m1.csv", digits: 1,
      ea_name: "PRO_fit_Band_EA", initial_deposit: 10000000, leverage: 10,
      lot_size: 0.5, ma_method: "sma", ma_period: 30, period: "P2",
      point_size: 0.1, stop_loss_points: 100, stops_level: 0, symbol: "JP225",
      take_profit_points: 200, volume_max: 100, volume_min: 0.01, volume_step: 0.01,
    },
  },
};

/** 入れ子ごとキーをソートする（並び順を比較対象から外す＝正規化）。 */
function sortDeep(v) {
  if (Array.isArray(v)) return v.map(sortDeep);
  if (v && typeof v === "object") {
    return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortDeep(v[k])]));
  }
  return v;
}

function routerFetch(schema) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    if (url === "/sim/settings-schema") {
      return schema
        ? { ok: true, status: 200, json: async () => schema }
        : { ok: false, status: 404, json: async () => ({ error: "no schema" }) };
    }
    if (url === "/sim/run-options") return { ok: true, status: 200, json: async () => RUN_OPTIONS };
    if (url === "/sim/jobs") return { ok: true, status: 202, json: async () => ({ job_id: "j1", status: "running" }) };
    return { ok: false, status: 404, json: async () => ({ error: "nope" }) };
  };
  fn.calls = calls;
  return fn;
}

const flush = () => new Promise((r) => setTimeout(r, 0));

/** 本番の合成根を組み、同一操作を入れて投入し、正規化した本文を返す。 */
async function submittedBody(schema) {
  const doc = fakeDoc();
  const fetchFn = routerFetch(schema);
  const warn = console.warn;
  console.warn = () => {};   // 縮退構成の掲示は本件の対象外（別検定が固定している）
  try {
    await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn });
  } finally {
    console.warn = warn;
  }
  const setById = (id, value) => { const n = findById(doc.body, id); if (n) n.value = value; };
  for (const f of EA_INPUT_IDS) setById(f.id, OPERATION[f.param]);
  if (!schema) setById(LEGACY_DEPOSIT_ID, OPERATION.initial_deposit);
  findById(doc.body, START_BUTTON_ID)._listeners.click[0]();
  await flush();
  const post = fetchFn.calls.find((c) => c.url === "/sim/jobs");
  assert.ok(post, "POST /sim/jobs が呼ばれていない");
  return sortDeep(JSON.parse(post.init.body));
}

// 操作する入力欄の所在は宣言表から引く（id をこの検定へ写さない）。
const { EA_INPUT_FIELDS } = await import("../js/adapter/front/sim_ea_inputs_panel_view.js");
const EA_INPUT_IDS = EA_INPUT_FIELDS;
/** 縮退面の初期資金欄（schema が無い構成でのみ存在する）。 */
const LEGACY_DEPOSIT_ID = "execDeposit";
/** 実行開始ボタン。 */
const START_BUTTON_ID = "runStart";

test("the settings configuration submits the S1 body verbatim", async () => {
  assert.deepEqual(await submittedBody(settingsSchema()), sortDeep(GOLDEN.settings));
});

test("the legacy configuration submits the S1 body verbatim", async () => {
  assert.deepEqual(await submittedBody(null), sortDeep(GOLDEN.legacy));
});
