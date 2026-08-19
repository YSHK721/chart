// settings_schema_client（Tester Settings schema の取得・Phase 8 スライス 5）の単体テスト。
//
// 固定する不変条件（`job_submit_client` と同流儀）:
//   1. 取得先は**同一オリジンの相対パス**（オリジン付き絶対 URL を組み立てない）。
//      統合 UI の routedFetch / Service Worker は mode prefix 付きのパスを冪等に扱う。
//   2. キャッシュを使わない（no-store）。schema は EA 登録・非対象宣言に追随する。
//   3. 非 2xx は**サーバ文言つきで throw** する。半端な payload を返して
//      「選択肢が 1 つも無いフォーム」を沈黙で作らない（fail-stop）。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  SETTINGS_SCHEMA_URL,
  SettingsSchemaError,
  createSettingsSchemaClient,
} from "../js/adapter/front/settings_schema_client.js";

function recordingFetch(res) {
  const calls = [];
  const fn = async (url, init) => { calls.push({ url, init }); return res; };
  fn.calls = calls;
  return fn;
}

test("load() GETs the schema from a same-origin relative path", async () => {
  // Arrange
  const fetchFn = recordingFetch({ ok: true, status: 200, json: async () => ({ ok: true, key_order: [] }) });
  const client = createSettingsSchemaClient({ fetch: fetchFn });
  // Act
  const payload = await client.load();
  // Assert
  assert.deepEqual(payload, { ok: true, key_order: [] });
  assert.equal(fetchFn.calls.length, 1);
  assert.equal(fetchFn.calls[0].url, SETTINGS_SCHEMA_URL);
  assert.ok(!/^[a-z][a-z0-9+.-]*:\/\//i.test(SETTINGS_SCHEMA_URL),
    "オリジン付きの絶対 URL を組み立てています（相対パスで書くこと）");
  assert.ok(SETTINGS_SCHEMA_URL.startsWith("/sim/"),
    "sim 名前空間の口ではありません（job_submit_client と同流儀にすること）");
});

test("the request does not use the cache (no-store)", async () => {
  const fetchFn = recordingFetch({ ok: true, status: 200, json: async () => ({ ok: true }) });
  await createSettingsSchemaClient({ fetch: fetchFn }).load();
  assert.equal(fetchFn.calls[0].init.cache, "no-store");
});

test("a non-2xx response throws with the server-supplied reason", async () => {
  // Arrange
  const client = createSettingsSchemaClient({
    fetch: async () => ({ ok: false, status: 503, json: async () => ({ error: "schema を組めません" }) }),
  });
  // Act / Assert
  await assert.rejects(() => client.load(), (e) => {
    assert.ok(e instanceof SettingsSchemaError, "SettingsSchemaError ではありません");
    assert.equal(e.status, 503);
    assert.match(e.message, /schema を組めません/);
    return true;
  });
});

test("a non-2xx with an unparsable body still throws with the status", async () => {
  const client = createSettingsSchemaClient({
    fetch: async () => ({ ok: false, status: 500, json: async () => { throw new Error("not json"); } }),
  });
  await assert.rejects(() => client.load(), (e) => {
    assert.ok(e instanceof SettingsSchemaError);
    assert.equal(e.status, 500);
    assert.match(e.message, /500/);
    return true;
  });
});
