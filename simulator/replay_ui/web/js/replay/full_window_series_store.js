// full_window_series_store.js — 「窓いっぱいで計算した系列（全長系列）」の単一保管庫（ISSUE-296）。
//
// 役割（1 つ）: 指標インスタンスごとに、**現在の窓に対する全長系列**を 1 本だけ保持する。
//   書き手は「全長計算が成立したとき」の 1 経路のみ、読み手は「同じ入力・同じ窓のときだけ」取り出す。
//
// なぜ要るか（ISSUE-296 実測）: モード切替（ライブ⇄リプレイ）は、チャート足・計算足・variant・
//   params・窓のいずれも変わっていないのに、算出済みの全長系列を破棄して全指標を計算し直していた。
//   その結果は実測でライブの系列と完全一致（23 指標・全点比較で差異 0）＝**捨てられる計算**であり、
//   バックエンドが重い処理を 1 スレッドで直列化する以上、所要時間は指標数に正比例していた
//   （実測: チャート足 1h・指標 5 個で 5.4〜8.9 秒）。保管庫は「同じものを二度計算しない」という
//   単一の規律でこれを消す。
//
// 同一性（キー）: 系列を決める入力すべて＝チャート足・計算足・variant・params・**窓**。窓は
//   `windowToken`（チャート足｜本数｜末尾足時刻）で表す。1 つでも違えば別物として扱い、
//   取り出せない（fail-closed＝古い系列を新しい窓へ流用しない）。
//
// 純ロジック（DOM/ネット非依存）。所有する状態は instanceId → 1 エントリのみ。
export class FullWindowSeriesStore {
  constructor() {
    this._byId = new Map();   // instanceId -> { key, def, params, series, _times }
  }

  // 全長系列を記録する（同一 instanceId の既存エントリは置き換える＝常に最新の 1 本だけ持つ）。
  put(instanceId, { key, def, params, series }) {
    if (!instanceId || !key || !Array.isArray(series)) {
      return;
    }
    this._byId.set(instanceId, { key, def, params, series, _times: null });
  }

  // キー（入力＋窓）が一致するときだけ返す。不一致・未記録は null（＝呼び出し側は従来経路へ）。
  get(instanceId, key) {
    const entry = this._byId.get(instanceId);
    return entry && entry.key === key ? entry : null;
  }

  // 系列名 → 昇順 time 配列（リビール基底のスライス位置探索に使う）。初回参照時に 1 度だけ作る。
  static timesOf(entry) {
    if (!entry) {
      return new Map();
    }
    if (!entry._times) {
      const times = new Map();
      for (const p of entry.series) {
        if (p && Array.isArray(p.data)) {
          times.set(p.name, p.data.map((pt) => pt.time));
        }
      }
      entry._times = times;
    }
    return entry._times;
  }

  forget(instanceId) {
    this._byId.delete(instanceId);
  }

  clear() {
    this._byId.clear();
  }

  size() {
    return this._byId.size;
  }
}
