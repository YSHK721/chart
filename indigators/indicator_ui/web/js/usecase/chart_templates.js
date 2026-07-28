// chart_templates.js — チャートテンプレート（usecase・純関数）。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §4.1（CHART_TEMPLATE / TEMPLATE_INSTANCE・保存しない属性）、§4.2（永続化スキーマ）、
//   §4.3（templateId 採番 `tpl#{seq}`・単調増加・破損時復旧）、§5.1（UC-T01 保存）、
//   §5.3（UC-T03 紐付け）、§5.4（UC-T04 再適用判定）、§5.5（UC-T05 改名・削除）、
//   §5.6（F-T1 / F-T3 / F-T6）、§7.1（DOM・Storage 非依存の純関数）、
//   §8.1 A-2（テンプレート最大 50 件・名前最大 40 文字）。
//
// 責務（SRP）: テンプレート集合の追加／更新／改名／削除、紐付け解決、
//   AppliedInstance ⇄ TEMPLATE_INSTANCE 写像、名前検証。
// 非責務: DOM・Storage・chart への一切のアクセス（import ゼロ＝最内層）。
//
// 純粋性の規律: すべての関数は入力（templates 配列・その要素・bindings）を破壊しない。
//   更新は新しい配列・オブジェクトを返す（呼び出し側が確定・永続化する）。

// §8.1 A-2 で確定した上限値。
export const MAX_TEMPLATES = 50;
export const MAX_NAME_LENGTH = 40;

// 検証・保存結果の code 語彙（呼び出し側が UI 文言へ写像する）。
export const CODE = Object.freeze({
  ok: 'ok',
  empty: 'empty',
  tooLong: 'too_long',
  duplicate: 'duplicate',
  limit: 'limit',
  notFound: 'not_found',
});

// ---------------------------------------------------------------------------
// 名前（§4.1 name・§5.1 処理 2・§5.5・F-T1）
// ---------------------------------------------------------------------------

// 正規化名 = trim ＋小文字化（§5.1 処理 2）。同名判定はこの値で行う。
export function normalizeTemplateName(name) {
  return String(name ?? '').trim().toLowerCase();
}

// 表示用に採用する名前 = trim のみ（表記は入力のまま・§5.1 処理 2「name は入力の表記を採用」）。
export function displayTemplateName(name) {
  return String(name ?? '').trim();
}

// 名前検証（§4.1: 1〜40 文字・前後空白は trim／§5.5: 正規化名の重複不可）。
//   excludeTemplateId: 改名時に「自分自身との一致」を重複から除外する（§5.5）。
export function validateTemplateName(name, { templates = [], excludeTemplateId = null } = {}) {
  const display = displayTemplateName(name);
  if (display.length === 0) {
    return { ok: false, code: CODE.empty };
  }
  if (display.length > MAX_NAME_LENGTH) {
    return { ok: false, code: CODE.tooLong };
  }
  const normalized = normalizeTemplateName(name);
  const clash = (templates ?? []).some(
    (t) => t && t.templateId !== excludeTemplateId && normalizeTemplateName(t.name) === normalized,
  );
  if (clash) {
    return { ok: false, code: CODE.duplicate };
  }
  return { ok: true, code: CODE.ok };
}

// ---------------------------------------------------------------------------
// 写像（§4.1 TEMPLATE_INSTANCE・保存しない属性）
// ---------------------------------------------------------------------------

// params（[k,v] ペア配列・facade 形／オブジェクト）を平坦オブジェクトへ正規化する。
//   既存 IndicatorController._paramsObject と同一の正規化（§4.1）。
export function paramsObject(params) {
  if (Array.isArray(params)) {
    return Object.fromEntries(params);
  }
  return { ...(params ?? {}) };
}

// AppliedInstance（または其の JSON 形）→ TEMPLATE_INSTANCE（§4.1 の 5 属性のみ）。
//   instanceId / seq / createdAt / generation / datasetRef / timeframe は保存しない
//   （§4.1「保存しない属性と理由」＝適用時の再採番・決定論性の担保）。
export function toTemplateInstance(instance) {
  return {
    indicatorId: instance.indicatorId,
    variant: instance.variant,
    params: paramsObject(instance.params),
    visible: !!instance.visible,
    styles: instance.styles ?? null,
  };
}

// TEMPLATE_INSTANCE[] → AppliedInstance の JSON 形[]（facade.deserialize が読む形）。
//   instanceId は `${indicatorId}#${seq}` で導出されるため、seq を seqCounters（§5.7 単調カウンタ）
//   で再採番する（§4.1「適用時に seqCounters で再採番する」＝系列名衝突の回避）。
//   入力 seqCounters は破壊せず、更新後のカウンタを併せて返す。
export function toAppliedJsonList(instances, seqCounters = {}) {
  const counters = { ...(seqCounters ?? {}) };
  const applied = (instances ?? []).map((t) => {
    const seq = (counters[t.indicatorId] ?? 0) + 1;
    counters[t.indicatorId] = seq;
    return {
      instanceId: `${t.indicatorId}#${seq}`,
      indicatorId: t.indicatorId,
      variant: t.variant,
      params: paramsObject(t.params),
      visible: !!t.visible,
      generation: 0,
      seq,
      createdAt: null,
      styles: t.styles ?? null,
    };
  });
  return { applied, seqCounters: counters };
}

// ---------------------------------------------------------------------------
// templateId 採番（§4.3）
// ---------------------------------------------------------------------------

// 次の templateId を発行する。形式 `tpl#{seq}`・seq は lastSeq + 1（§4.3）。
export function nextTemplateId(lastSeq) {
  const seq = (Number.isInteger(lastSeq) ? lastSeq : 0) + 1;
  return { templateId: `tpl#${seq}`, lastSeq: seq };
}

// templateSeq.v1 破損時の復旧（§4.3）: 初期化後の lastSeq を、templates 内の既存 `tpl#N` の
//   最大 N 以上へ引き上げる（id の再利用・衝突を避ける）。
export function recoverLastSeq(lastSeq, templates = []) {
  let max = Number.isInteger(lastSeq) ? lastSeq : 0;
  for (const t of templates ?? []) {
    const m = /^tpl#(\d+)$/.exec(t && t.templateId ? String(t.templateId) : '');
    if (m) {
      const n = Number(m[1]);
      if (Number.isInteger(n) && n > max) {
        max = n;
      }
    }
  }
  return max;
}

// ---------------------------------------------------------------------------
// UC-T01 保存（§5.1）
// ---------------------------------------------------------------------------

// 現在の適用済み構成をテンプレートとして保存する。
//   - 正規化名が既存と一致 → その既存テンプレートを上書き更新（templateId 保持＝紐付けは維持・
//     name は入力の表記・createdAt 不変・updatedAt 更新）。
//   - 一致しない → 新規採番して追加（上限 50 件で拒否・§5.1 例外「上書き更新は可」）。
export function saveTemplate({ templates = [], lastSeq = 0, name, applied = [], now = 0 } = {}) {
  const list = templates ?? [];
  const existing = list.find((t) => t && normalizeTemplateName(t.name) === normalizeTemplateName(name));
  // 重複は「上書き」であって検証エラーではないため、自分自身を除外して検証する。
  const verdict = validateTemplateName(name, {
    templates: list,
    excludeTemplateId: existing ? existing.templateId : null,
  });
  if (!verdict.ok) {
    return { ok: false, code: verdict.code, templates: list, lastSeq, templateId: null };
  }
  const instances = (applied ?? []).map(toTemplateInstance);
  if (existing) {
    const updated = {
      ...existing,
      name: displayTemplateName(name),
      instances,
      updatedAt: now,
    };
    return {
      ok: true,
      code: CODE.ok,
      templates: list.map((t) => (t.templateId === existing.templateId ? updated : t)),
      lastSeq,
      templateId: existing.templateId,
    };
  }
  if (list.length >= MAX_TEMPLATES) {
    return { ok: false, code: CODE.limit, templates: list, lastSeq, templateId: null };
  }
  const issued = nextTemplateId(lastSeq);
  const created = {
    templateId: issued.templateId,
    name: displayTemplateName(name),
    instances,
    createdAt: now,
    updatedAt: now,
  };
  return {
    ok: true,
    code: CODE.ok,
    templates: [...list, created],
    lastSeq: issued.lastSeq,
    templateId: issued.templateId,
  };
}

// ---------------------------------------------------------------------------
// UC-T05 改名・削除（§5.5）
// ---------------------------------------------------------------------------

// 改名: name を検証（1〜40 文字・他テンプレートとの正規化名重複不可）して更新し updatedAt を進める。
//   templateId・紐付け・activeTemplateId は不変（本関数は templates のみを返す）。
export function renameTemplate({ templates = [], templateId, name, now = 0 } = {}) {
  const list = templates ?? [];
  const target = list.find((t) => t && t.templateId === templateId);
  if (!target) {
    return { ok: false, code: CODE.notFound, templates: list };
  }
  const verdict = validateTemplateName(name, { templates: list, excludeTemplateId: templateId });
  if (!verdict.ok) {
    return { ok: false, code: verdict.code, templates: list };
  }
  const updated = { ...target, name: displayTemplateName(name), updatedAt: now };
  return {
    ok: true,
    code: CODE.ok,
    templates: list.map((t) => (t.templateId === templateId ? updated : t)),
  };
}

// 削除: 当該テンプレートを除去し、当該 id を参照する紐付けを削除、activeTemplateId が
//   当該 id なら null にする（§5.5）。現在チャート上の構成は変更しない（本関数は状態を返すだけ）。
export function deleteTemplate({ templates = [], bindings = {}, templateId, activeTemplateId = null } = {}) {
  const list = templates ?? [];
  const nextBindings = {};
  for (const [tf, id] of Object.entries(bindings ?? {})) {
    if (id !== templateId) {
      nextBindings[tf] = id;
    }
  }
  return {
    templates: list.filter((t) => !t || t.templateId !== templateId),
    bindings: nextBindings,
    activeTemplateId: activeTemplateId === templateId ? null : activeTemplateId,
  };
}

// ---------------------------------------------------------------------------
// UC-T03 紐付け設定・解除（§5.3）
// ---------------------------------------------------------------------------

// 当該時間足の紐付けを設定（templateId）または解除（null）した bindings を返す。
//   入力 bindings は破壊しない。紐付け操作そのものは構成を変更しない（§5.3）。
export function setBinding({ bindings = {}, timeframe, templateId = null } = {}) {
  const next = { ...(bindings ?? {}) };
  if (templateId === null || templateId === undefined || templateId === '') {
    delete next[timeframe];
  } else {
    next[timeframe] = templateId;
  }
  return next;
}

// ---------------------------------------------------------------------------
// UC-T04 紐付け解決・再適用判定（§5.4・F-T3・F-T6）
// ---------------------------------------------------------------------------

// 切替先時間足に対して「適用すべきテンプレート id」を解決する。
//   templateId === null は「適用しない」（＝現行挙動を維持する）。
//   - 有効時間足集合外のキーは解決対象にしない・削除もしない（F-T6 将来足の温存）。
//   - 参照先テンプレートが不在（dangling）なら適用せず、当該紐付けを削除する（F-T3 遅延クリーンアップ）。
//   - 解決した id が activeTemplateId と同一なら適用しない（§5.4 発火条件 3・決定論性）。
//   changed は bindings に変更（dangling クリーンアップ）が生じたかを示す（呼び出し側が永続化判断に使う）。
export function resolveBinding({
  bindings = {}, templates = [], timeframe, validTimeframes = null, activeTemplateId = null,
} = {}) {
  const next = { ...(bindings ?? {}) };
  const valid = Array.isArray(validTimeframes) ? new Set(validTimeframes) : null;
  if (valid && !valid.has(timeframe)) {
    return { templateId: null, bindings: next, changed: false };
  }
  const boundId = next[timeframe];
  if (boundId === undefined || boundId === null) {
    return { templateId: null, bindings: next, changed: false };
  }
  const exists = (templates ?? []).some((t) => t && t.templateId === boundId);
  if (!exists) {
    delete next[timeframe];
    return { templateId: null, bindings: next, changed: true };
  }
  if (boundId === activeTemplateId) {
    return { templateId: null, bindings: next, changed: false };
  }
  return { templateId: boundId, bindings: next, changed: false };
}
