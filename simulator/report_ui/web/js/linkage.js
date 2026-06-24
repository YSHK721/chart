// 双方向ハイライト中枢（詳細設計 §11.1 linkage.js / アーキ指針 §3）。
// hoverTradeId / activeFilter の単一状態を保持し、状態遷移を購読者へ通知する。
// DOM 副作用は購読者（main.js が登録: 行ハイライト・マーカー再描画・ローソク減光）に委譲し、
// linkage 自体は DOM 非依存の純状態機械とする（table / chart はこれを一方向 import）。

export function createLinkage() {
  const store = {
    hoverTradeId: null,
    // F-3用・F-2では未配線: activeFilter / applyFilter / subscribeFilter は
    // F-3（ヒートマップ/抽出）用スキャフォールド。F-2 では呼ばれず常に null。
    activeFilter: null, // Set<number> | null
    _hoverSubs: [],
    _filterSubs: [],

    subscribe(fn) {
      this._hoverSubs.push(fn);
    },
    // F-3用・F-2では未配線（抽出フィルタ購読）。
    subscribeFilter(fn) {
      this._filterSubs.push(fn);
    },

    // hover 対象 trade id を設定（id===null で解除）。同一 id は早期 return（再通知しない）。
    setHover(id, source) {
      if (this.hoverTradeId === id) return;
      this.hoverTradeId = id;
      for (const fn of this._hoverSubs) fn(id, source);
    },

    // F-3用・F-2では未配線。抽出フィルタを設定（空 Set / null は null 化）。
    applyFilter(ids, label) {
      this.activeFilter = ids && ids.size ? ids : null;
      for (const fn of this._filterSubs) fn(this.activeFilter, label);
    },
  };
  return store;
}
