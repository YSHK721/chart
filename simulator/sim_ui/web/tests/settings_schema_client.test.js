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

// --- 200 でも payload が schema でなければ失敗として扱う（fail-open の起動条件）------
// なぜ: 呼出側（合成根）は「例外＝schema 無し」で旧フォームを権威に残す。ここで `null` や
// 形不正を**成功**として返すと、fail-open が作動しないまま Tester パネルが結線され、
// EA 欄・初期資金欄が器から外れた**投入不能フォーム**になる（実測済みの死因）。

test("a 200 with an unparsable body throws instead of returning null", async () => {
  // Arrange: プロキシのエラーページ等（HTTP は 200 でも本文が JSON でない）
  const client = createSettingsSchemaClient({
    fetch: async () => ({ ok: true, status: 200, json: async () => { throw new Error("Unexpected token <"); } }),
  });
  // Act / Assert
  await assert.rejects(() => client.load(), (e) => {
    assert.ok(e instanceof SettingsSchemaError, "200＋非 JSON を成功として返しています");
    assert.equal(e.status, 200);
    return true;
  });
});

test("a 200 whose payload is not a schema (ok !== true) throws", async () => {
  for (const payload of [null, {}, { ok: false, error: "台帳がありません" }, []]) {
    const client = createSettingsSchemaClient({
      fetch: async () => ({ ok: true, status: 200, json: async () => payload }),
    });
    await assert.rejects(() => client.load(), (e) => {
      assert.ok(e instanceof SettingsSchemaError, `形不正 payload を成功として返しています: ${JSON.stringify(payload)}`);
      return true;
    });
  }
});

test("the thrown reason prefers the server-supplied error text", async () => {
  const client = createSettingsSchemaClient({
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ ok: false, error: "台帳がありません" }) }),
  });
  await assert.rejects(() => client.load(), (e) => {
    assert.match(e.message, /台帳がありません/);
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
