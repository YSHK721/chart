// sim_submission_builder（投入契約・純関数・Phase 9 S2 M5）の単体テスト。
//
// 投入本文の組み立て規則をここ 1 箇所に閉じる（View にも合成根にも第 2 実装を置かない）。
// 純関数なので DOM も fetch も要らない＝node:test から素で呼べる（構造ガードは
// import_source.test.js が「doc / fetch を import しない」ことを機械強制する）。
//
// 固定する不変条件:
//   1. backtest は 18 キー完全（profile 由来 11 ＋ 実行対象 2 ＋ EA パラメータ 5）。
//   2. profile 由来キーは注入 profile からのみ来る（front リテラル 0）。
//   3. `strategy` は**常に不在**（Phase 9 S1 で UI 出口を撤去したため）。
//   4. `settings` は null なら本文へ載せない（旧フォーム投入と byte 等価）。
//   5. resolveProfile は symbol 一致の**先頭**を返し、一致が無ければ null（決定的）。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  PROFILE_KEYS, buildSubmission, resolveProfile,
} from "../js/adapter/front/sim_submission_builder.js";

const PROFILE = Object.freeze({
  dataset: "jp225_m1", data_path: "/d/jp225_m1.csv", symbol: "JP225", period: "M1",
  contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
  volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: 0,
});
const SUBJECT = Object.freeze({ ea_name: "TC24051901", initial_deposit: 10000, settings: null });
const INPUTS = Object.freeze({
  stop_loss_points: 100, take_profit_points: 200,
  ma_period: 20, ma_method: "ema", lot_size: 0.1,
});
const SUBJECT_KEYS = ["ea_name", "initial_deposit"];

// --- 1/2. backtest 18 キー完全・profile 由来は注入のみ ---------------------------

test("buildSubmission returns the full 18-key backtest body", () => {
  const bt = buildSubmission({ profile: PROFILE, subject: SUBJECT, inputs: INPUTS }).backtest;
  assert.deepEqual(
    Object.keys(bt).sort(),
    [...PROFILE_KEYS, ...SUBJECT_KEYS, ...Object.keys(INPUTS)].sort(),
  );
  assert.equal(Object.keys(bt).length, 18);
});

test("profile-derived keys come only from the injected profile", () => {
  const other = { ...PROFILE, symbol: "OTHER", contract_size: 1.0, point_size: 0.001 };
  const bt = buildSubmission({ profile: other, subject: SUBJECT, inputs: INPUTS }).backtest;
  for (const k of PROFILE_KEYS) assert.strictEqual(bt[k], other[k], k);
});

test("a profile carrying config_overrides passes it through untouched", () => {
  const withOverrides = { ...PROFILE, config_overrides: { entry_price_basis: "close" } };
  const bt = buildSubmission({ profile: withOverrides, subject: SUBJECT, inputs: INPUTS }).backtest;
  assert.deepEqual(bt.config_overrides, { entry_price_basis: "close" });
});

test("with no profile the backtest carries only the subject and the EA inputs", () => {
  const bt = buildSubmission({ profile: null, subject: SUBJECT, inputs: INPUTS }).backtest;
  assert.deepEqual(Object.keys(bt).sort(), [...SUBJECT_KEYS, ...Object.keys(INPUTS)].sort());
});

// --- 3. strategy は常に不在 -------------------------------------------------------

test("the body never carries a strategy block (S1 で UI 出口を撤去した)", () => {
  const body = buildSubmission({ profile: PROFILE, subject: SUBJECT, inputs: INPUTS });
  assert.equal("strategy" in body, false);
  assert.deepEqual(Object.keys(body), ["backtest"]);
});

// --- 4. settings は null なら非搭載 ----------------------------------------------

test("a null settings block is omitted from the body (旧フォーム投入と byte 等価)", () => {
  const body = buildSubmission({
    profile: PROFILE, subject: { ...SUBJECT, settings: null }, inputs: INPUTS,
  });
  assert.equal("settings" in body, false);
});

test("a present settings block is carried through verbatim", () => {
  const settings = { tester: { Expert: "AAA.zzz" }, inputs: [] };
  const body = buildSubmission({ profile: PROFILE, subject: { ...SUBJECT, settings }, inputs: INPUTS });
  assert.deepEqual(body.settings, settings);
  assert.deepEqual(Object.keys(body).sort(), ["backtest", "settings"]);
});

// --- 5. resolveProfile ------------------------------------------------------------

test("resolveProfile returns the first dataset whose symbol matches", () => {
  const a = { ...PROFILE, dataset: "a", symbol: "JP225" };
  const b = { ...PROFILE, dataset: "b", symbol: "JP225" };
  const c = { ...PROFILE, dataset: "c", symbol: "OTHER" };
  assert.strictEqual(resolveProfile([c, a, b], "JP225"), a);
});

test("resolveProfile returns null when no dataset carries that symbol", () => {
  assert.strictEqual(resolveProfile([PROFILE], "NOPE"), null);
});

test("resolveProfile returns null for an empty or missing dataset list", () => {
  assert.strictEqual(resolveProfile([], "JP225"), null);
  assert.strictEqual(resolveProfile(null, "JP225"), null);
  assert.strictEqual(resolveProfile(undefined, "JP225"), null);
});

test("resolveProfile returns null for a blank symbol (既定の当てはめをしない)", () => {
  assert.strictEqual(resolveProfile([PROFILE], ""), null);
  assert.strictEqual(resolveProfile([PROFILE], null), null);
});

test("resolveProfile compares symbols as strings (型で取りこぼさない)", () => {
  const numeric = { ...PROFILE, symbol: 225 };
  assert.strictEqual(resolveProfile([numeric], "225"), numeric);
});
