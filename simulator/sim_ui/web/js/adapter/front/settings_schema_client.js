// Tester Settings フォームの schema 取得クライアント（Phase 8 スライス 5・F-8 系）。
//
// 取得先は sim core の schema 面 `GET /sim/settings-schema`（Phase 8 スライス 2 の口・新設なし）。
// **同一オリジンの相対パス**で書くのは job_submit_client / report_source_client と同じ理由で、
// 統合 UI の routedFetch / Service Worker が mode prefix 付きのパスを冪等に扱うためである。
// fetch は注入して実行とテストを分ける。
//
// 責務（SRP）: HTTP だけ。payload の解釈（何が選択肢で何が必須か）は持たない——それは
// サーバ側カタログ（`adapter/tester_settings_schema_catalog.py`）が列挙から導出する単一
// ソースであり、front はその写しを作らない（基本設計 §18.3「複製ゼロの機械検査」）。
//
// 非 2xx を throw にする理由: 半端な payload を返すと「選択肢が 1 つも無いフォーム」が
// 沈黙で出来上がる。取れなかったことは呼出側（合成根）が fail-open の判断に使う。

/** Tester Settings schema の取得先（sim core の schema 面）。 */
export const SETTINGS_SCHEMA_URL = "/sim/settings-schema";

/** 取得失敗（非 2xx）のエラー。サーバの error 文言を握って上位へ届ける。 */
export class SettingsSchemaError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "SettingsSchemaError";
    this.status = status;
  }
}

/** schema 取得クライアントを作る。 */
export function createSettingsSchemaClient({ fetch: fetchFn } = {}) {
  const doFetch = fetchFn || ((...args) => globalThis.fetch(...args));

  return {
    /**
     * schema（{ok, key_order, required_keys, enum_options, scalar_specs,
     * expert_options, unsupported}）を返す。
     *
     * **schema を返せないときは必ず throw する**（`null` や形不正を成功にしない）。
     * 呼出側は「例外＝schema 無し」を fail-open の起動条件に使うため、ここで成功に
     * 化けさせると、空の schema でパネルが結線され、EA 欄・初期資金欄が器から外れた
     * 投入不能フォームが黙って出来上がる。判定は 3 点:
     *   1. 応答が無い / 非 2xx
     *   2. 本文を JSON として読めない（プロキシのエラーページ等・HTTP は 200 になり得る）
     *   3. payload が schema の契約（`ok: true`）を満たさない
     */
    async load() {
      const res = await doFetch(SETTINGS_SCHEMA_URL, { cache: "no-store" });
      let payload = null;
      try {
        payload = res && res.json ? await res.json() : null;
      } catch (_e) {
        payload = null;
      }
      const ok = res && res.ok && payload && payload.ok === true;
      if (!ok) {
        const reason =
          (payload && payload.error) || `設定 schema を取得できません (HTTP ${res && res.status})`;
        throw new SettingsSchemaError(reason, res && res.status);
      }
      return payload;
    },
  };
}
