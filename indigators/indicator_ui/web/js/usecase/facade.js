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
    styles: base.styles ?? null,
    ...overrides,
  });
}

// スタイル整合（ISSUE-110 🔴-1）: styles のキーを「現在の実系列名集合」と突合し、実系列に
//   存在しない stale キー（params 変更で系列が改名された等）を剪定した新 state を返す。
//   剪定対象が無ければ同一 state（無変更）。全キー剪定で空になったら styles=null へ戻す。
//   currentNames が空集合のとき（描画前・renderer 未対応）は判定不能のため剪定しない。
export function reconcileSeriesStyles(state, instanceId, currentNames) {
  const names = currentNames instanceof Set ? currentNames : new Set(currentNames ?? []);
  if (names.size === 0) {
    return state;
  }
  const inst = state.applied.find((i) => i.instanceId === instanceId);
  const styles = inst && inst.styles;
  if (!styles) {
    return state;
  }
  const staleKeys = Object.keys(styles).filter((n) => !names.has(n));
  if (staleKeys.length === 0) {
    return state;
  }
  const next = cloneState(state);
  next.applied = next.applied.map((i) => {
    if (i.instanceId !== instanceId) {
      return i;
    }
    const kept = {};
    for (const [n, v] of Object.entries(styles)) {
      if (names.has(n)) {
        kept[n] = v;
      }
    }
    return rebuildInstance(i, { styles: Object.keys(kept).length > 0 ? kept : null });
  });
  return next;
}

// スタイル上書き（ISSUE-109）: 系列名 -> { color?, width?, style?, visible? } の差分 patch を
//   既存 styles へフィールド単位でマージした新 state を返す。patch が空なら無変更。
export function setSeriesStyles(state, instanceId, patch) {
  if (!patch || Object.keys(patch).length === 0) {
    return state;
  }
  const next = cloneState(state);
  next.applied = next.applied.map((i) => {
    if (i.instanceId !== instanceId) {
      return i;
    }
    const merged = { ...(i.styles ?? {}) };
    for (const [name, fields] of Object.entries(patch)) {
      merged[name] = { ...(merged[name] ?? {}), ...fields };
    }
    return rebuildInstance(i, { styles: merged });
  });
  return next;
}

export function remove(state, instanceId) {
  const next = cloneState(state);
  // applied から除去。seqCounters は減算しない（seq 再利用しない §5.7）。
  next.applied = next.applied.filter((i) => i.instanceId !== instanceId);
  return next;
}

/**
 * ペインの並び順（ドラッグ&ドロップの結果）を applied 配列の順序へ反映した新 state を返す。
 *
 * なぜ applied の順序なのか（ユーザー指示「永続化しろ」2026-08-09）:
 *   復元は `IndicatorStateStore._restoreRun` → `rebuildApplied(state.applied)` が **配列順に**
 *   指標を再適用し、`SeriesDrawer._ensurePane` が pane 指標 1 件につき pane を 1 枚ずつ末尾へ
 *   追加する。つまり並び順の表現は「applied 配列の順序」として**既に 1 つだけ存在する**。
 *   別キーへ順序を保存すると同じ事実の第 2 の表現が生まれ、両者がずれた瞬間にどちらが正か
 *   決められなくなる。ここを直せば保存キーも復元手順も増えず、applied を保存する
 *   チャートテンプレートも並び順を自動で持つ。
 *
 * 並べ替えの規則: **instanceIds に挙がった要素が占めている添字の集合だけ**を、その順序で
 *   詰め直す。挙がっていない要素（pane を持たない overlay 指標）は元の添字に留まるため、
 *   価格ペインの凡例行の並び（＝適用順）を壊さない。未知 id は無視し、挙がらなかった要素も
 *   落とさない（冪等・防御的）。
 *
 * @param {object} state          現在の state（破壊しない）。
 * @param {string[]} instanceIds  ペイン順に並んだ pane 指標の instanceId。
 * @returns {object}              並べ替え後の新 state。
 */
export function reorderApplied(state, instanceIds) {
  const next = cloneState(state);
  const byId = new Map(next.applied.map((i) => [i.instanceId, i]));
  // 実在する id だけを、渡された順に（重複は初出のみ採る）。
  const seen = new Set();
  const ordered = (instanceIds ?? []).filter((id) => {
    if (!byId.has(id) || seen.has(id)) {
      return false;
    }
    seen.add(id);
    return true;
  });
  // 並べ替えの対象となる添字（＝いま対象 instance が占めている枠）。
  const positions = [];
  next.applied.forEach((inst, idx) => {
    if (seen.has(inst.instanceId)) {
      positions.push(idx);
    }
  });
  for (let k = 0; k < positions.length && k < ordered.length; k += 1) {
    next.applied[positions[k]] = byId.get(ordered[k]);
  }
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
    styles: i.styles ?? null,
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
