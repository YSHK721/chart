// UC ファサード（usecase/facade.js）。
//
// UC-01 listForView / UC-02 apply / UC-03 recompute / UC-04 toggleVisible /
// UC-05 remove / UC-06 toggleFavorite / UC-07 serialize/deserialize を集約する。
// ComputeGateway はポート注入（テストでは Fake gateway を渡す）。
// DOM/chart/fetch/localStorage 非依存の純ロジック。
//   - listForView: タブ∧カテゴリ∧検索∧お気に入りの論理積（§4.6）
//   - recompute: nextGeneration()→compute→accepts() 真なら採用・偽なら破棄（§6.6）
//   - serialize/deserialize: §6.1 物理スキーマの純粋なオブジェクト⇔JSON 変換

import { AppliedInstance } from '../domain/domain_models.js';
import { get, list } from './catalog.js';

// 状態の空既定（§6.1 物理スキーマに対応するメモリ表現）。
export function emptyState() {
  return {
    applied: [],
    favorites: [],
    seqCounters: {},
    uiState: { lastTab: 'indicator', lastCategory: '', dialogOpen: false },
  };
}

// ---------------------------------------------------------------------------
// UC-01 listForView（論理積フィルタ §4.6）
// ---------------------------------------------------------------------------
export function listForView({ tab, category, query, favoriteOnly, favorites } = {}) {
  const favoriteSet = new Set(favorites ?? []);
  return list().filter((d) => {
    if (tab !== undefined && tab !== null && d.tab !== tab) {
      return false;
    }
    if (category !== undefined && category !== null && d.category.nameKey !== category) {
      return false;
    }
    if (favoriteOnly && !favoriteSet.has(d.id)) {
      return false;
    }
    // 検索は表示名 key + id を対象（domain は i18n 解決器を持たないため key を表示名相当に渡す）。
    if (query !== undefined && query !== null && !d.matches(query, d.displayNameKey)) {
      return false;
    }
    return true;
  });
}

// ---------------------------------------------------------------------------
// UC-04 toggleVisible / UC-05 remove / UC-06 toggleFavorite
// ---------------------------------------------------------------------------
function cloneState(state) {
  return {
    applied: [...state.applied],
    favorites: [...state.favorites],
    seqCounters: { ...state.seqCounters },
    uiState: { ...state.uiState },
  };
}

export function toggleVisible(state, instanceId) {
  const next = cloneState(state);
  next.applied = next.applied.map((i) =>
    i.instanceId === instanceId ? rebuildInstance(i, { visible: !i.visible }) : i,
  );
  return next;
}

// AppliedInstance 再構築の単一集約点（不変オブジェクトの部分差し替え）。
// base の全フィールドを引き継ぎ、overrides で指定された値のみ上書きする。
// 振る舞い不変: 既存の withVisible / apply / recompute / instanceFromJson の
// 個別再構築をこの 1 関数へ集約（フィールド集合・既定は従来と同一）。
function rebuildInstance(base, overrides = {}) {
  return new AppliedInstance({
    indicatorId: base.indicatorId,
    variant: base.variant,
    params: base.params,
    visible: base.visible,
    generation: base.generation,
    seq: base.seq,
    createdAt: base.createdAt,
    ...overrides,
  });
}

export function remove(state, instanceId) {
  const next = cloneState(state);
  // applied から除去。seqCounters は減算しない（seq 再利用しない §5.7）。
  next.applied = next.applied.filter((i) => i.instanceId !== instanceId);
  return next;
}

export function toggleFavorite(state, indicatorId) {
  const next = cloneState(state);
  if (next.favorites.includes(indicatorId)) {
    next.favorites = next.favorites.filter((id) => id !== indicatorId);
  } else {
    next.favorites = [...next.favorites, indicatorId];
  }
  return next;
}

// ---------------------------------------------------------------------------
// UC-02 apply / UC-03 recompute（ComputeGateway ポート注入・generation 競合破棄 §6.6）
// ---------------------------------------------------------------------------

// seq 採番（§5.7）: next = (counters[indicatorId] ?? 0) + 1。同時にカウンタを更新。
function nextSeq(state, indicatorId) {
  const current = state.seqCounters[indicatorId] ?? 0;
  const next = current + 1;
  state.seqCounters[indicatorId] = next;
  return next;
}

export async function apply(state, { indicatorId, variant, params, datasetRef }, gateway) {
  const next = cloneState(state);
  const seq = nextSeq(next, indicatorId);
  const instance = new AppliedInstance({
    indicatorId,
    variant,
    params: paramsToPairs(params),
    visible: true,
    generation: 0,
    seq,
    createdAt: '2026-06-07T00:00:00Z',
  });
  // compute（generation=0）。ChartRenderer 描画は adapter（スコープ外）。
  await gateway.compute({ indicatorId, variant, params, datasetRef, generation: 0 });
  next.applied = [...next.applied, instance];
  return { state: next, instance };
}

export async function recompute(state, instanceId, newParams, datasetRef, gateway) {
  const next = cloneState(state);
  const idx = next.applied.findIndex((i) => i.instanceId === instanceId);
  if (idx === -1) {
    return { state: next, accepted: false };
  }
  const current = next.applied[idx];
  // next_generation()→compute→accepts() 真なら採用・偽なら破棄（§6.6）。
  const advanced = current.nextGeneration();
  const result = await gateway.compute({
    indicatorId: advanced.indicatorId,
    variant: advanced.variant,
    params: newParams,
    datasetRef,
    generation: advanced.generation,
  });
  if (advanced.accepts(result.generation)) {
    // 採用: instance を新世代（advanced）＋新パラメータで差し替え。
    next.applied = next.applied.map((i, k) =>
      k === idx ? rebuildInstance(advanced, { params: paramsToPairs(newParams) }) : i,
    );
    return { state: next, accepted: true };
  }
  // 破棄: 古い/未来の応答は反映しない。applied は据え置き（current のまま）。
  return { state: next, accepted: false };
}

function paramsToPairs(params) {
  if (Array.isArray(params)) {
    return params;
  }
  return Object.entries(params ?? {});
}

// ---------------------------------------------------------------------------
// UC-07 永続化（§6.1/§5.6 — 純粋なオブジェクト⇔JSON 変換。localStorage は触らない）
// ---------------------------------------------------------------------------

function instanceToJson(i) {
  return {
    instanceId: i.instanceId,
    indicatorId: i.indicatorId,
    variant: i.variant,
    params: i.params,
    visible: i.visible,
    generation: i.generation,
    seq: i.seq,
    createdAt: i.createdAt,
  };
}

function instanceFromJson(o) {
  // 永続化 JSON（plain object）→ AppliedInstance。フィールド集合は §6.1 と一致するため
  // rebuildInstance に委譲（base に plain object を渡し、全フィールドを引き継ぐ）。
  return rebuildInstance(o);
}

export function serialize(state) {
  return JSON.stringify({
    applied: state.applied.map(instanceToJson),
    favorites: state.favorites,
    seqCounters: state.seqCounters,
    uiState: state.uiState,
  });
}

export function deserialize(json) {
  const base = emptyState();
  let parsed;
  try {
    parsed = JSON.parse(json);
  } catch {
    return base;
  }
  // §6.2: 当該キーのみ初期化・全消去しない（破壊的変更回避）。
  if (Array.isArray(parsed?.applied)) {
    base.applied = parsed.applied.map(instanceFromJson);
  }
  if (Array.isArray(parsed?.favorites)) {
    base.favorites = parsed.favorites;
  }
  if (parsed?.seqCounters && typeof parsed.seqCounters === 'object' && !Array.isArray(parsed.seqCounters)) {
    base.seqCounters = parsed.seqCounters;
  }
  if (parsed?.uiState && typeof parsed.uiState === 'object' && !Array.isArray(parsed.uiState)) {
    base.uiState = parsed.uiState;
  }
  // §5.7 不変条件の補正 + 既存破損データの治癒（同一指標 2 本のパラメータ汚染バグ対策）。
  //   instanceId は `${indicatorId}#${seq}` で導出され、衝突すると recompute の findIndex が
  //   常に先頭一致を返すため、2 本目の編集が 1 本目を書き換える。原因は 2 つ:
  //   (1) seqCounters がコントローラ restore で永続化されず空で渡るため、リロード後に同一指標を
  //       再追加すると seq が 1 に戻り既存と衝突する。
  //   (2) 上記バグ版が既に同一 (indicatorId, seq) を複数保存しており、復元時点で instanceId が
  //       重複している（カウンタ補正だけでは治らない）。
  //   対策: 復元順に重複 seq を「当該指標の最大 seq + 1」へ再採番して一意化し、最終的な最大 seq
  //   までカウンタを底上げする。再採番は冪等（重複なしなら無変更）。
  const usedSeqByIndicator = new Map();
  const maxSeqByIndicator = new Map();
  base.applied = base.applied.map((inst) => {
    const id = inst.indicatorId;
    if (!usedSeqByIndicator.has(id)) {
      usedSeqByIndicator.set(id, new Set());
      maxSeqByIndicator.set(id, 0);
    }
    const used = usedSeqByIndicator.get(id);
    let seq = inst.seq;
    if (typeof seq !== 'number' || used.has(seq)) {
      seq = maxSeqByIndicator.get(id) + 1; // 重複・不正 seq は再採番
    }
    used.add(seq);
    if (seq > maxSeqByIndicator.get(id)) {
      maxSeqByIndicator.set(id, seq);
    }
    return seq === inst.seq ? inst : rebuildInstance(inst, { seq });
  });
  for (const [id, maxSeq] of maxSeqByIndicator) {
    const current = base.seqCounters[id] ?? 0;
    if (maxSeq > current) {
      base.seqCounters[id] = maxSeq;
    }
  }
  return base;
}
