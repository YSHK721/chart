// chart_bars_client（足の成果物の取得・ISSUE-552/554 段階 2-2）の単体テスト（fake fetch）。
//
// 固定する不変条件:
//   1. URL の綴りがサーバ（`chart_bars_api_controller.py`）の綴りと一致する（front が送る形を
//      サーバが受けなければ無言で死ぬ・ISSUE-291）。サーバのソースを読んで突き合わせる。
//   2. 非 2xx・JSON 不正・契約外（ok !== true）は throw し、状態を `status` に載せる。
//   3. 取得は問うた回数だけ（発行 − 問い = 0）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  CHART_BARS_BASE, ChartBarsError, createChartBarsClient, extentUrl, rowsUrl,
} from "../js/adapter/front/chart_bars_client.js";

function fakeFetch(response) {
  const calls = [];
  const fn = async (url, init) => { calls.push({ url, init }); return response; };
  fn.calls = calls;
  return fn;
}
const ok = (payload) => ({ ok: true, status: 200, json: async () => payload });
const failed = (status, payload) => ({ ok: false, status, json: async () => payload });

/** サーバのソースから、文字列定数の値を読む（NAME = "value"）。 */
function serverConstant(name) {
  const path = fileURLToPath(new URL("../../adapter/chart_bars_api_controller.py", import.meta.url));
  const m = new RegExp(`^${name} = "([^"]+)"`, "m").exec(readFileSync(path, "utf-8"));
  assert.ok(m, `サーバのソースに ${name} が見つかりません（検定が空振り）`);
  return m[1];
}

test("URL の綴りはサーバの綴りと一致する（prefix・extent・rows）", () => {
  const prefix = serverConstant("CHART_BARS_PATH_PREFIX");
  assert.equal(CHART_BARS_BASE, `/sim${prefix}`);
  assert.equal(extentUrl("j1"), `/sim${prefix}/j1/${serverConstant("_EXTENT_SEGMENT")}`);
  assert.equal(rowsUrl("j1", 5, 9), `/sim${prefix}/j1/${serverConstant("_ROWS_SEGMENT")}/5/9`);
});

test("extent / rows は応答の payload をそのまま返す", async () => {
  const payload = { ok: true, rows: 3 };
  const fetchFn = fakeFetch(ok(payload));
  const client = createChartBarsClient({ fetch: fetchFn });
  assert.equal(await client.extent("j1"), payload);
  assert.equal(await client.rows("j1", 0, 3), payload);
  assert.deepEqual(fetchFn.calls.map((c) => c.url), [extentUrl("j1"), rowsUrl("j1", 0, 3)]);
});

for (const status of [400, 404, 409, 413]) {
  test(`非 2xx（${status}）は throw し、状態とサーバの文言を運ぶ`, async () => {
    const client = createChartBarsClient({ fetch: fakeFetch(failed(status, { error: "理由" })) });
    await assert.rejects(client.extent("j1"), (err) => {
      assert.ok(err instanceof ChartBarsError);
      assert.equal(err.status, status);
      assert.equal(err.message, "理由");
      return true;
    });
  });
}

test("JSON として読めない・ok が true でない応答は throw する（半端な応答を成功にしない）", async () => {
  const broken = { ok: true, status: 200, json: async () => { throw new Error("bad json"); } };
  await assert.rejects(createChartBarsClient({ fetch: fakeFetch(broken) }).rows("j1", 0, 1), ChartBarsError);
  await assert.rejects(createChartBarsClient({ fetch: fakeFetch(ok({ rows: 1 })) }).rows("j1", 0, 1), ChartBarsError);
});

test("計算量: 取得の発行 − 問うた回数 = 0（問う回数 2 点）", async () => {
  for (const n of [1, 25]) {
    const fetchFn = fakeFetch(ok({ ok: true }));
    const client = createChartBarsClient({ fetch: fetchFn });
    for (let i = 0; i < n; i += 1) await client.rows("j1", i, i + 1);
    assert.equal(fetchFn.calls.length - n, 0);
  }
});
