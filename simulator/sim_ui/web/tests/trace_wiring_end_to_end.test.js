// 実行トレースの投入経路が front の**端から端まで**結線されていることの機械強制
// （ISSUE-508 段階 3 §6.6.2 / ISSUE-291 の再発防止）。
//
// なぜ在るか: 投入契約（M5 `traceBlockOf`）とクライアント（`job_submit_client`）に口を作った
//   だけでは機能は存在しない。**呼出元が実際に渡している**ことを誰も確かめていなければ、
//   HTTP は 202 を返し run は成功し、トレースだけが無言で出ない——ISSUE-291 と同型である。
//   したがってここは純関数を素で呼ばない。**本番の合成根を組み、画面の操作から
//   `POST /sim/jobs` の本文まで**を通して固定する。
//
// 固定する不変条件:
//   1. 画面で ON にして期間を打つと、本文の `trace` に epoch 秒が載る。
//   2. ON ＋期間空は「窓なし＝全区間」（サーバから見て `start` / `end` が未指定）。
//   3. OFF は `trace` を送らない。トレース面が在っても本文の既存部分は 1 バイトも動かない。
//   4. 片側だけの指定・逆転した期間は**投入しない**（サーバまで運ばず、理由を画面に出す）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten, IDLE_WATCH_TIMER } from "./_fakes.js";
import { settingsSchema } from "./_settings_schema_fixture.js";
import { mountSimExecutionPanel } from "../js/adapter/front/composition_root_execution.js";

const flush = () => new Promise((r) => setTimeout(r, 0));

const RUN_OPTIONS = {
  ok: true,
  datasets: [{
    dataset: "jp225_m1", data_path: "/d/jp225_m1.csv", symbol: "JP225", period: "M1",
    contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
    volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: 0,
  }],
  ea_names: ["PRO_fit_Band_EA", "TC24051901"],
};

function routerFetch() {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    if (url === "/sim/settings-schema") {
      return { ok: true, status: 200, json: async () => settingsSchema() };
    }
    if (url === "/sim/run-options") return { ok: true, status: 200, json: async () => RUN_OPTIONS };
    if (url === "/sim/jobs") {
      return { ok: true, status: 202, json: async () => ({ job_id: "j1", status: "running" }) };
    }
    return { ok: false, status: 404, json: async () => ({ error: "nope" }) };
  };
  fn.calls = calls;
  return fn;
}

/** 本番の合成根を組み、trace 欄へ `spec` を打ち、スタートを押す。 */
async function submitWith(spec) {
  const doc = fakeDoc();
  const fetchFn = routerFetch();
  const errors = [];
  const consoleError = console.error;
  console.error = (m) => errors.push(String(m));
  let refs;
  try {
    refs = await mountSimExecutionPanel({
      ...IDLE_WATCH_TIMER, doc, host: doc.body, fetch: fetchFn,
    });
    if (spec) {
      findById(doc.body, "traceEnabled").checked = !!spec.enabled;
      findById(doc.body, "traceFrom").value = spec.from || "";
      findById(doc.body, "traceTo").value = spec.to || "";
    }
    findById(doc.body, "runStart")._listeners.click[0]();
    await flush();
  } finally {
    console.error = consoleError;
  }
  if (refs && refs.dispose) refs.dispose();
  const post = fetchFn.calls.find((c) => c.url === "/sim/jobs");
  return { doc, errors, post, body: post ? JSON.parse(post.init.body) : null };
}

/** 掲示面に出ている文言をすべて連結して返す（押した結果が画面に出ているかの実証）。 */
const noticeText = (doc) =>
  flatten(findById(doc.body, "simRunStatusPanel")).map((n) => n.textContent || "").join(" ");

// --- 1. ON ＋期間 → 本文に epoch 秒が載る -----------------------------------

test("switching the trace on carries the period to POST /sim/jobs as epoch seconds", async () => {
  const { body } = await submitWith({ enabled: true, from: "2025.01.06", to: "2025.01.10" });
  assert.ok(body, "POST /sim/jobs が呼ばれていない");
  // 終了日 2025.01.10 は**その日を含む**（裁定 2026-09-10）ので、上端は翌日 0 時。
  assert.deepEqual(body.trace, {
    enabled: true,
    start: Date.parse("2025-01-06T00:00:00Z") / 1000,
    end: Date.parse("2025-01-11T00:00:00Z") / 1000,
  });
  assert.deepEqual(body.trace, { enabled: true, start: 1736121600, end: 1736553600 });
});

// --- 2. ON ＋期間空 → 窓なし（全区間）---------------------------------------

test("switching it on with no period asks for the whole run (窓なし＝全区間)", async () => {
  const { body } = await submitWith({ enabled: true, from: "", to: "" });
  assert.equal(body.trace.enabled, true);
  // サーバは `block.get("start")` で読む（`job_models.trace_window_bounds`）。鍵が無い
  // ことと `null` であることは、そこから先で区別されない＝どちらも「窓なし」である。
  assert.equal(body.trace.start ?? null, null);
  assert.equal(body.trace.end ?? null, null);
});

// --- 3. OFF → `trace` を送らない（既存挙動と byte 等価）----------------------

test("leaving the trace off submits no trace block at all", async () => {
  const { body } = await submitWith(null);
  assert.equal("trace" in body, false);
  // 正の対照: 本文そのものは組まれ、送られている。
  assert.ok(body.backtest.ea_name);
});

test("the trace panel changes nothing else in the submitted body", async () => {
  const off = await submitWith(null);
  const on = await submitWith({ enabled: true, from: "2025.01.06", to: "2025.01.10" });
  assert.deepEqual(on.body.backtest, off.body.backtest);
  assert.deepEqual(on.body.settings, off.body.settings);
  assert.deepEqual(
    Object.keys(on.body).sort(),
    [...Object.keys(off.body), "trace"].sort(),
    "trace 以外の鍵が増減しています（既存投入と byte 等価でない）",
  );
});

// --- 4. 片側だけ・逆転は投入しない -------------------------------------------

test("a one-sided period is refused before the submission leaves the screen", async () => {
  for (const spec of [
    { enabled: true, from: "2025.01.06", to: "" },
    { enabled: true, from: "", to: "2025.01.10" },
  ]) {
    const { post, doc } = await submitWith(spec);
    assert.equal(post, undefined, "片側だけの期間がサーバまで運ばれています");
    assert.match(noticeText(doc), /trace/, "拒んだ理由が画面に出ていません");
  }
});

test("an inverted period is refused before the submission leaves the screen", async () => {
  const { post, doc } = await submitWith({ enabled: true, from: "2025.01.10", to: "2025.01.06" });
  assert.equal(post, undefined, "逆転した期間がサーバまで運ばれています");
  assert.match(noticeText(doc), /trace/, "拒んだ理由が画面に出ていません");
});

test("an off trace with leftover text in the fields still submits (OFF が権威)", async () => {
  // 正の対照: 上の 2 件は「期間が悪いと投入しない」であって「投入できなくなる」ではない。
  // チェックが OFF なら欄に何が残っていても従来どおり投入される。
  const { post, body } = await submitWith({ enabled: false, from: "2025.01.10", to: "2025.01.06" });
  assert.ok(post, "OFF なのに投入が止まっています");
  assert.equal("trace" in body, false);
});
