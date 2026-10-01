// 実行中のジョブを開いたときの完了待ち（ISSUE-540・job_completion_wait.js）。
//
// 実測（2026-09-27・実 UI）: 投入直後に「結果を見る」を押すと report.json / chart_overlay.json が
//   409 になり、完了しても誰も読み直さず、取引明細 0 行・売買履歴チャート空のまま残った。
//
// 固定する仕様:
//   - 「実行中」を掲示し、ジョブ状態の監視を張る（監視は呼び出し 1 回につき 1 本＝計算量）。
//   - 完了（terminal かつ completed）で reload を 1 回呼ぶ（第 2 の組み立て経路を作らない）。
//   - 失敗・取消は理由を掲示し、reload しない。監視の断念は「監視が止まった」と掲示する。
//   - 合成根（node からは import できない・配信パス import を含む）は not_ready でこの部品を
//     呼び、destroy で停止関数を呼ぶ——ソース構造で固定する（import_source.test.js と同じ流儀）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { showRunningAndWaitForCompletion } from "../js/adapter/front/job_completion_wait.js";

function harness() {
  const messages = [];
  const spy = { watched: [], stops: 0, onUpdate: null, reloads: 0 };
  spy.watch = (jobId, onUpdate) => {
    spy.watched.push(jobId);
    spy.onUpdate = onUpdate;
    return () => { spy.stops += 1; };
  };
  const stop = showRunningAndWaitForCompletion({
    view: { showMessage: (t) => messages.push(t) },
    statusClient: spy,
    jobId: "job-1",
    reload: () => { spy.reloads += 1; },
  });
  return { messages, spy, stop };
}

test("「実行中」を掲示し、そのジョブの監視を張る", () => {
  const { messages, spy } = harness();
  assert.match(messages.at(-1), /実行中/);
  assert.deepEqual(spy.watched, ["job-1"]);
});

test("完了（terminal・completed）で reload を 1 回呼ぶ。実行中の更新では呼ばない", () => {
  const { spy } = harness();
  spy.onUpdate({ status: "running", terminal: false });
  assert.equal(spy.reloads, 0);
  spy.onUpdate({ status: "completed", terminal: true });
  assert.equal(spy.reloads, 1);
});

test("失敗は理由を掲示し、reload しない", () => {
  const { messages, spy } = harness();
  spy.onUpdate({ status: "failed", terminal: true, failure_reason: "終了コード 2" });
  assert.match(messages.at(-1), /終了コード 2/);
  assert.equal(spy.reloads, 0);
});

test("理由の無い終端（取消等）は状態名で掲示する", () => {
  const { messages, spy } = harness();
  spy.onUpdate({ status: "cancelled", terminal: true, failure_reason: null });
  assert.match(messages.at(-1), /cancelled/);
});

test("監視の断念は「監視が止まった」ことを掲示する（ジョブの終端と書かない）", () => {
  const { messages, spy } = harness();
  spy.onUpdate({ error: "状態を取得できません (HTTP 500)" });
  assert.match(messages.at(-1), /監視を継続できません/);
  assert.equal(spy.reloads, 0);
});

test("計算量: 呼び出し 1 回につき監視 1 本・実行中の更新を何度受けても増えない", () => {
  const { spy } = harness();
  for (let i = 0; i < 10; i += 1) spy.onUpdate({ status: "running", terminal: false });
  assert.equal(spy.watched.length, 1);
  assert.equal(spy.reloads, 0);
});

test("戻り値は監視の停止関数そのもの", () => {
  const { spy, stop } = harness();
  stop();
  assert.equal(spy.stops, 1);
});

// ---- 合成根の結線（ソース構造・import_source.test.js と同じ流儀） ----

const ROOT_SRC = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "js", "adapter", "front", "composition_root_front.js"),
  "utf8",
);

test("合成根は not_ready でこの部品を呼び、destroy で監視を止める", () => {
  assert.match(ROOT_SRC, /from "\.\/job_completion_wait\.js"/);
  const notReady = ROOT_SRC.indexOf('e.code === "not_ready"');
  assert.ok(notReady >= 0, "not_ready の分岐が無い");
  const call = ROOT_SRC.indexOf("stopJobWatch = showRunningAndWaitForCompletion(");
  assert.ok(call > notReady, "not_ready の分岐で完了待ちを張っていない");
  assert.match(ROOT_SRC, /if \(stopJobWatch\) \{ stopJobWatch\(\); stopJobWatch = null; \}/);
});
