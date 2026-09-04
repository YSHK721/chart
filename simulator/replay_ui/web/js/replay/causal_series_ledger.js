// causal_series_ledger.js — 「その時点で描いた値」を確定として保持する台帳（ISSUE-293）。
//
// 役割（1 つ）: リプレイの描画系列に対し、**過去（リビール時点 T より前のバー）の点は最初に
//   描いた値のまま固定**し、T のバーだけを新しい値で更新・記録する。
//
// なぜ要るか: 上位足計算の指標は「進行中の期間」の値が期間の進行とともに動くため、毎フレーム
//   全点を再計算すると過去のバーの値まで最新値へ塗り替わる。これでは「その時点で何が見えて
//   いたか」を画面から検証できない（未来の混入が起きていても、見た目には出ない）。台帳は
//   描画の記録性を回復し、混入があれば「開始直後から当日の確定値が出ている」形で可視化する。
//
// 純ロジック（DOM/ネット非依存）。所有する状態は instanceId → 系列名 → time → 点 のみ。
export class CausalSeriesLedger {
  constructor() {
    this._byInstance = new Map();   // instanceId -> Map(seriesName -> Map(time -> point))
    this._lastT = null;
  }

  // 系列へ台帳を被せて返す。t（そのフレームの時点）より前の点は記録済みの値を優先し、
  //   t の点は新しい値で更新・記録する。t が未設定（ライブ）なら素通し（記録もしない）。
  //   time を持たない点（horizontal_line 等）は対象外＝そのまま。
  apply(instanceId, series, t) {
    if (t == null || !Array.isArray(series)) {
      return series;
    }
    const perInstance = this._ensure(instanceId);
    return series.map((p) => {
      if (!p || !Array.isArray(p.data)) {
        return p;
      }
      const recorded = this._ensureSeries(perInstance, p.name);
      const data = p.data.map((pt) => {
        if (!pt || typeof pt.time !== 'number') {
          return pt;
        }
        if (pt.time < t) {
          const kept = recorded.get(pt.time);
          if (kept !== undefined) {
            return kept;          // 確定済み＝更新しない
          }
          recorded.set(pt.time, pt);   // 初出（基底構築時など）はこの値を確定とする
          return pt;
        }
        recorded.set(pt.time, pt);     // 現在のバーは動いてよい（確定は次フレーム以降）
        return pt;
      });
      return { ...p, data };
    });
  }

  // リビール時点の更新。巻き戻し（t が後退）したら、その先の記録を捨てる（再生し直すと
  //   もう一度その時点の値で確定される）。前進のときは何もしない（走査費用ゼロ）。
  setTime(t) {
    if (t == null) {
      this._lastT = null;
      return;
    }
    if (this._lastT != null && t < this._lastT) {
      this.truncateAfter(t);
    }
    this._lastT = t;
  }

  truncateAfter(t) {
    for (const perInstance of this._byInstance.values()) {
      for (const recorded of perInstance.values()) {
        for (const time of [...recorded.keys()]) {
          if (time > t) {
            recorded.delete(time);
          }
        }
      }
    }
  }

  // params/variant 変更・削除で当該インスタンスの記録を捨てる（別の系列になるため）。
  forget(instanceId) {
    this._byInstance.delete(instanceId);
  }

  // 時間足切替など、全系列が別物になるとき。
  clear() {
    this._byInstance.clear();
    this._lastT = null;
  }

  _ensure(instanceId) {
    let m = this._byInstance.get(instanceId);
    if (!m) {
      m = new Map();
      this._byInstance.set(instanceId, m);
    }
    return m;
  }

  _ensureSeries(perInstance, name) {
    const key = name ?? '';
    let m = perInstance.get(key);
    if (!m) {
      m = new Map();
      perInstance.set(key, m);
    }
    return m;
  }
}
