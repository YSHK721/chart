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
//   6. symbolCandidatesOf は datasets から選べる銘柄を出現順で 1 つずつ返す（重複を畳む）。
//   7. seriesCandidatesOf は畳んだ先の系列（`RunProfile.dataset`）を返し、分岐が実在しない
//      （1 本だけの）銘柄では空を返す。resolveProfile は指定された系列を解決し、指定が
//      無ければ従来どおり銘柄一致の先頭を返す（投入本文は 1 バイトも増えない）。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  PROFILE_KEYS, buildSubmission, resolveProfile, seriesCandidatesOf, symbolCandidatesOf,
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
  // 例に使う値は決定論設定の語彙にあるもの（建値基準は ISSUE-533 段階 2 で語彙から外れた）。
  const withOverrides = { ...PROFILE, config_overrides: { tick_model: "ohlc_expand" } };
  const bt = buildSubmission({ profile: withOverrides, subject: SUBJECT, inputs: INPUTS }).backtest;
  assert.deepEqual(bt.config_overrides, { tick_model: "ohlc_expand" });
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

// --- 6. symbolCandidatesOf --------------------------------------------------------
// resolveProfile と対（datasets から実行対象を引く規則）。合成根に置くと、規則なのに
// 器と通信のダブル無しでは確かめられなくなる（M5 が引き受ける理由そのもの）。

test("symbolCandidatesOf lists the selectable symbols in dataset order", () => {
  const a = { ...PROFILE, dataset: "a", symbol: "BBB" };
  const b = { ...PROFILE, dataset: "b", symbol: "AAA" };
  assert.deepEqual(symbolCandidatesOf([a, b]), ["BBB", "AAA"]);
});

test("symbolCandidatesOf folds several datasets of the same symbol into one", () => {
  const a = { ...PROFILE, dataset: "a", symbol: "JP225" };
  const b = { ...PROFILE, dataset: "b", symbol: "JP225" };
  assert.deepEqual(symbolCandidatesOf([a, b]), ["JP225"]);
});

test("symbolCandidatesOf yields strings (候補は select の値＝文字列)", () => {
  assert.deepEqual(symbolCandidatesOf([{ ...PROFILE, symbol: 225 }]), ["225"]);
});

test("symbolCandidatesOf returns an empty list for an empty or missing dataset list", () => {
  assert.deepEqual(symbolCandidatesOf([]), []);
  assert.deepEqual(symbolCandidatesOf(null), []);
  assert.deepEqual(symbolCandidatesOf(undefined), []);
});

// --- 7. 系列の軸（ISSUE-511 段階 8-D-5）------------------------------------------
// 同じ銘柄に複数のデータセット（系列）が在るとき、symbolCandidatesOf はそれを 1 候補へ
// 畳む（候補は「選べる銘柄」であって「データセットの数」ではない）。畳んだ先を選び直す
// 第 2 の軸がこれである。識別子は `RunProfile.dataset`（ref 名）であり PROFILE_KEYS に
// 含まれない＝投入本文のキーにはならない。
//
// 軸を出すのは**実在する分岐のときだけ**である（候補 1 本なら空を返す＝画面は現行と同一）。
// 「出すか出さないか」は規則であって器の都合ではないため、判定は M5 が持つ（View は
// 銘柄候補と同じく「候補が在れば出す」だけを見る＝しきい値の第 2 実装を作らない）。

test("seriesCandidatesOf lists the dataset refs of that symbol in run-options order", () => {
  const a = { ...PROFILE, dataset: "zzz_alpha", symbol: "JP225" };
  const b = { ...PROFILE, dataset: "zzz_beta", symbol: "JP225" };
  const other = { ...PROFILE, dataset: "zzz_other", symbol: "OTHER" };
  assert.deepEqual(seriesCandidatesOf([other, a, b], "JP225"), ["zzz_alpha", "zzz_beta"]);
});

test("seriesCandidatesOf yields no axis when the symbol carries a single series", () => {
  // 実在しない分岐を画面に出さない（認知負荷の最小化＝候補 1 本なら現行画面と同一）。
  assert.deepEqual(seriesCandidatesOf([PROFILE], PROFILE.symbol), []);
  const other = { ...PROFILE, dataset: "zzz_other", symbol: "OTHER" };
  assert.deepEqual(seriesCandidatesOf([PROFILE, other], PROFILE.symbol), []);
});

test("seriesCandidatesOf yields no axis for an empty / missing / unknown input", () => {
  assert.deepEqual(seriesCandidatesOf([], "JP225"), []);
  assert.deepEqual(seriesCandidatesOf(null, "JP225"), []);
  assert.deepEqual(seriesCandidatesOf(undefined, "JP225"), []);
  assert.deepEqual(seriesCandidatesOf([PROFILE, { ...PROFILE, dataset: "b" }], "NOPE"), []);
});

test("seriesCandidatesOf yields strings (候補は select の値＝文字列)", () => {
  const a = { ...PROFILE, dataset: 1, symbol: "JP225" };
  const b = { ...PROFILE, dataset: 2, symbol: "JP225" };
  assert.deepEqual(seriesCandidatesOf([a, b], "JP225"), ["1", "2"]);
});

test("resolveProfile follows the chosen series instead of the first of that symbol", () => {
  const a = { ...PROFILE, dataset: "zzz_alpha", symbol: "JP225" };
  const b = { ...PROFILE, dataset: "zzz_beta", symbol: "JP225" };
  assert.strictEqual(resolveProfile([a, b], "JP225", "zzz_beta"), b);
  assert.strictEqual(resolveProfile([a, b], "JP225", "zzz_alpha"), a);
});

test("resolveProfile with no series keeps the first of that symbol (既存投入と同一)", () => {
  const a = { ...PROFILE, dataset: "zzz_alpha", symbol: "JP225" };
  const b = { ...PROFILE, dataset: "zzz_beta", symbol: "JP225" };
  for (const series of [undefined, null, ""]) {
    assert.strictEqual(resolveProfile([a, b], "JP225", series), a, String(series));
  }
});

test("resolveProfile returns null for a series that symbol does not carry", () => {
  // 既定へ当てはめない（当てはめると「選んでいない系列で回った」ことが画面から分からない）。
  const a = { ...PROFILE, dataset: "zzz_alpha", symbol: "JP225" };
  const b = { ...PROFILE, dataset: "zzz_beta", symbol: "JP225" };
  assert.strictEqual(resolveProfile([a, b], "JP225", "zzz_nope"), null);
});

test("resolveProfile keeps the series axis inside the chosen symbol", () => {
  // 系列は銘柄の内側の軸である。別銘柄の ref を指定しても、その銘柄へ乗り換えない。
  const a = { ...PROFILE, dataset: "zzz_alpha", symbol: "JP225" };
  const foreign = { ...PROFILE, dataset: "zzz_foreign", symbol: "OTHER" };
  assert.strictEqual(resolveProfile([a, foreign], "JP225", "zzz_foreign"), null);
});

test("the series axis name is not a submitted key (投入本文は 1 バイトも増えない)", () => {
  assert.equal(PROFILE_KEYS.includes("dataset"), false);
  const a = { ...PROFILE, dataset: "zzz_alpha", symbol: "JP225" };
  const b = { ...PROFILE, dataset: "zzz_beta", symbol: "JP225" };
  const first = buildSubmission({ profile: a, subject: SUBJECT, inputs: INPUTS });
  const second = buildSubmission({ profile: b, subject: SUBJECT, inputs: INPUTS });
  assert.deepEqual(Object.keys(second.backtest).sort(), Object.keys(first.backtest).sort());
  for (const key of Object.keys(second.backtest)) {
    assert.notEqual(key, "dataset", "系列の識別子が投入本文に載っています");
  }
});
