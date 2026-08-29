// template_binding_reader（adapter/front/template_binding_reader.js）
//   — 注入された **読み取り専用** storage から、8 時間足ぶんの instance 束を組む。
//
// 設計入力:
//   - arch-spec §0 T-2: 統合ページの合成根が live スコープの storage を読み取り専用で注入する。
//     **View は自分でスコープを選ばない**（束の出所がページごとに散るため）。組んだ束は
//     Input Model の一部としてサーバへ送る（arch-spec §9 の `instances`）。
//   - 設計書 §3.4: 時間足 ↔ テンプレートの紐付け。`1m` と `1M` が同一テンプレートを共有するのは
//     **正しい設定**（依頼者確認済み 2026-08-28）なので、共有を異常として扱わない。
//   - 参照実装（束の組み方）: tools/measure/issue449/probe_inverse.py:62-77。
//     `binds[tf]` でテンプレートを引き、その `instances` を回し、`params.timeframe` から軸を
//     解決する（`"chart"` または未指定は表示足に従う＝§2 の chart 追従水準）。
//
// キー名・値の形は既存 adapter が唯一源であり、ここでは**写さずに同じ物理キーを読む**:
//   indigators/indicator_ui/web/js/adapter/front/local_storage_template_gateway.js
//     `indicatorUi.templates.v1`        → {templates: [{templateId, name, instances: [...]}]}
//     `indicatorUi.templateBindings.v1` → {bindings: {<timeframe>: <templateId>}}
//   instance の属性は chart_templates.js の `toTemplateInstance`
//     （indicatorId / variant / params / visible / styles。instanceId は**保存されない**）。
//   物理キーの接頭辞（`live:`）は注入された scopedStorage 側が付ける。ここでは付けない
//     （付けると二重になる）。
//
// 無言縮退の禁止（設計書 §5.2 / §7 と同じ規約）: 紐付けが無い・JSON が壊れている・紐付け先の
//   テンプレートが無い・未知の時間足が混ざっている場合は、空の束を静かに返さず**理由つきの
//   エラー**を返す。空で返すと「水準が 1 つも無い相場」と区別が付かなくなる。

import { DASHBOARD_TIMEFRAMES } from './timeframes.js';

/** 読み取る論理キー（接頭辞は注入された storage が付ける）。 */
export const TEMPLATE_STORAGE_KEYS = Object.freeze({
  templates: 'indicatorUi.templates.v1',
  bindings: 'indicatorUi.templateBindings.v1',
});

// 表示する 8 時間足の並びは timeframes.js が唯一源（第 2 表の列と同じ並びであること）。
//   束と列で別々の写しを持つと、片方だけ足したときに無言でずれる。
export { DASHBOARD_TIMEFRAMES } from './timeframes.js';

/** params の `timeframe` に置ける「表示足に従う」の表明（§2 chart 追従水準）。 */
const FOLLOW_CHART = 'chart';

/** 失敗を「理由つき」で返す（例外を投げずに返すのは、呼び出し側が掲示に使うため）。 */
function failure(message) {
  return { ok: false, instances: [], error: { message } };
}

/** storage の 1 キーを JSON として読む。壊れていればキー名を含む理由で落とす。 */
function readJson(storage, key) {
  let raw;
  try {
    raw = storage.getItem(key);
  } catch (err) {
    throw new Error(`テンプレートの読み取りに失敗しました（${key}）: ${err && err.message ? err.message : err}`);
  }
  if (raw === null || raw === undefined) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error(`テンプレートの保存内容が壊れています（${key}）`);
  }
}

/**
 * 読み取り専用 storage から instance 束を組む。
 *
 * @param {object} opts
 * @param {{getItem: Function}} opts.storage 注入された読み取り専用 storage（T-2）
 * @returns {{ok: boolean, instances: Array<object>, error?: {message: string}}}
 *   成功時 `instances` の各要素は arch-spec §9 の `instances` 契約
 *   （`instance_id` / `indicator_id` / `variant` / `params` / `timeframe`）に、
 *   表示上の出所を示す `timeframe_binding`（どの列の紐付けから来たか）を添えた形。
 */
export function readInstanceBundle({ storage } = {}) {
  if (!storage || typeof storage.getItem !== 'function') {
    return failure('テンプレートの保存領域が注入されていません（統合ページの合成根が渡します）');
  }

  let templatesDoc;
  let bindingsDoc;
  try {
    templatesDoc = readJson(storage, TEMPLATE_STORAGE_KEYS.templates);
    bindingsDoc = readJson(storage, TEMPLATE_STORAGE_KEYS.bindings);
  } catch (err) {
    return failure(err.message);
  }

  const bindings = bindingsDoc && typeof bindingsDoc.bindings === 'object' && bindingsDoc.bindings
    ? bindingsDoc.bindings
    : null;
  if (!bindings || Object.keys(bindings).length === 0) {
    return failure(
      '時間足とチャートテンプレートの紐付けがありません（ライブ画面でテンプレートを時間足へ割り当ててください）',
    );
  }

  const templates = Array.isArray(templatesDoc?.templates) ? templatesDoc.templates : [];
  const byId = new Map(templates.filter((t) => t && t.templateId).map((t) => [t.templateId, t]));

  const unknown = Object.keys(bindings).filter((tf) => !DASHBOARD_TIMEFRAMES.includes(tf));
  if (unknown.length > 0) {
    return failure(
      `紐付けに未知の時間足があります: ${unknown.join(', ')}（対象は ${DASHBOARD_TIMEFRAMES.join(' / ')}）`,
    );
  }

  const instances = [];
  // 走査は **DASHBOARD_TIMEFRAMES の順**で行う（保存の列挙順に依存させない＝読むたびに
  //   instance_id が入れ替わらない。到達時刻の同一性が読み直しで揺れる原因になる）。
  for (const timeframe of DASHBOARD_TIMEFRAMES) {
    const templateId = bindings[timeframe];
    if (templateId === undefined || templateId === null) {
      continue;   // 未紐付けの足は列を持たない（§9-6 の tpl#2 と同じ状態・異常ではない）。
    }
    const template = byId.get(templateId);
    if (!template) {
      return failure(`紐付け先のテンプレートがありません: ${templateId}（時間足 ${timeframe}）`);
    }
    const templateInstances = Array.isArray(template.instances) ? template.instances : [];
    // 同一テンプレートを複数の足が共有しても衝突しないよう、instance_id は
    //   「紐付いた足 / 指標 / テンプレート内の順番」から決定的に作る（保存側は instanceId を
    //   持たない＝chart_templates.js の `toTemplateInstance`）。
    const seqByIndicator = new Map();
    for (const raw of templateInstances) {
      if (!raw || !raw.indicatorId) {
        continue;
      }
      const indicatorId = String(raw.indicatorId);
      const seq = (seqByIndicator.get(indicatorId) ?? 0) + 1;
      seqByIndicator.set(indicatorId, seq);
      const params = { ...(raw.params ?? {}) };
      const declared = params.timeframe;
      delete params.timeframe;
      const axis = !declared || declared === FOLLOW_CHART ? timeframe : String(declared);
      instances.push({
        instance_id: `${timeframe}/${indicatorId}#${seq}`,
        indicator_id: indicatorId,
        variant: raw.variant ? String(raw.variant) : 'default',
        params,
        timeframe: axis,
        timeframe_binding: timeframe,
      });
    }
  }

  return { ok: true, instances };
}
