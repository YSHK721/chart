// market_profile_dwell_accumulator.js — DwellAccumulator（依存ゼロ純ロジック・domain）。
//
// 設計入力: Phase2 設計 mp_ticklive_design.md「新規 domain DwellAccumulator」。参照実装
//   prototype_260630-01/mp_core.py（_active_seconds_cross / _session_dwell / _value_area）と数値一致。
//
// 役割: base（確定足までの累積・不変）へ、forming 期間の tick を 1 本ずつ増分累積し、combined = base +
//   forming の表示プロファイル（POC/VA/bins）を snapshot で返す。per-tick HTTP を行わずクライアント側で
//   ローカルに増分する（サーバは base(GRID_W 固定グリッド) + forming tick 列 + active table を初回のみ供給）。
//
// 忠実 binning（mp_core.compute_profile と厳密一致）:
//   base も forming も **GRID_W 固定グリッド**（fine grid・k=floor(mid/gridW)）へ累積し、snapshot で
//   combined fine（base+forming）を表示 bin へ再集計してから POC/VA を出す。base=表示 bin 再集計・
//   forming=表示 bin 直接 の二方式併存は、広レンジ（binw≫gridW）や price_min が gridW の倍数でないとき
//   POC/VA を最大 5bin ずらす（実 tick で実証）。両者を同一 fine grid に統一してこの乖離を消す
//   ＝本番 market_profile_dwell.compute_dwell_profile（全窓）と POC/VA/形が一致する。
//
// dwell 原子（mp_core と同基準）:
//   dwell[i] = 隣接 tick 間ギャップ [sec[i], sec[i+1]) のうち「活発(曜日×時)に属する秒数」を
//   tick i の価格 mid[i] の fine bin k=floor(mid[i]/gridW) へ帰属させる。最新 tick は次 tick 未着まで dwell=0。

// バリューエリア比率（mp_core `_value_area` va_pct と同値・単一定義）。
export const VA_PCT = 0.70;

// [a, b) のうち活発な (曜日×時) に属する秒数を時間境界で積分する（mp_core._active_seconds_cross 相当）。
//   wd = ((floor(t/86400))+3)%7（1970-01-01=木を Mon0 基準へ）、hod = (t%86400)//3600。
export function activeSeconds(a, b, table) {
  let total = 0;
  let t = Math.trunc(a);
  const end = Math.trunc(b);
  while (t < end) {
    const nextHour = (Math.floor(t / 3600) + 1) * 3600;
    const seg = Math.min(nextHour, end);
    const wd = ((Math.floor(t / 86400) + 3) % 7 + 7) % 7;
    const hod = Math.floor((t % 86400) / 3600);
    if (table[wd] && table[wd][hod]) {
      total += seg - t;
    }
    t = seg;
  }
  return total;
}

// バリューエリアの下限/上限中心価格を返す（mp_core._value_area 相当・DRY の単一定義）。
//   tpo 降順（同値は index 昇順で決定論化）にビンを積み、累積が総 tpo×va_pct に達するまでのビン集合の
//   中心価格の最小/最大を [va_low, va_high] として返す。空/総和 0 は端の中心へ退化。
//
// MP-02 タイブレーク方針（意図的選択・決定論性の担保）:
//   参照 mp_core._value_area は `np.argsort(tpo)[::-1]`（quicksort・unstable）で降順化するため、同値
//   tpo のビン群の相対順序が非決定論的である。同値 bin 群が VA 70% 閾値を跨ぐ配置では、mp_core 側は
//   実行毎に採用 bin が変わり得る＝JS と厳密一致は元来不能。よって本実装は「降順・同値は index 昇順」
//   （下の sort 比較子 (tpo[j]-tpo[i]) || (i-j)）を**意図的に決定論的**な方針として採用する。これは
//   mp_core の非決定論を再現するのではなく、決定論性を優先した意図的分岐である（実害は低い＝同値 bin は
//   価格が隣接し VA 端の 1bin 差に留まる）。下段の「境界 tie golden」テストがこの方針を回帰的に固定する。
export function valueArea(centers, tpo, vaPct) {
  // 標準 Market Profile の VA（ISSUE-271）: POC を起点に、隣接する側のうち重みが大きい方へ
  //   1 ビンずつ連続的に広げ、累積が総重み×vaPct へ達したところで止める。
  //   かつては「重み降順に非連続で積み、採用集合の min/max」を返しており、Python の
  //   tf_period 側（標準 MP）と定義が食い違っていた（実測 79.2% 不一致）。
  //   Python market_profile._value_area と**同一規約**（同値は上側優先・POC 同値は index 昇順）。
  const n = tpo.length;
  if (n === 0) {
    return [0, 0];
  }
  let total = 0;
  for (let i = 0; i < n; i += 1) {
    total += tpo[i];
  }
  if (total <= 0) {
    return [Number(centers[0]), Number(centers[n - 1])];
  }
  // POC: 重み最大（同値は index 昇順＝価格の低い側）。
  let poc = 0;
  for (let i = 1; i < n; i += 1) {
    if (tpo[i] > tpo[poc]) {
      poc = i;
    }
  }
  const threshold = total * vaPct;
  let lo = poc;
  let hi = poc;
  let acc = tpo[poc];
  while (acc < threshold && (lo > 0 || hi < n - 1)) {
    const down = lo > 0 ? tpo[lo - 1] : -Infinity;
    const up = hi < n - 1 ? tpo[hi + 1] : -Infinity;
    if (up >= down) {
      hi += 1;
      acc += up;
    } else {
      lo -= 1;
      acc += down;
    }
  }
  return [Number(centers[lo]), Number(centers[hi])];
}


export class DwellAccumulator {
  constructor() {
    this._ready = false;
  }

  // base（GRID_W 固定グリッド dwell 配列・不変）／active table／レンジ／formingStart を受けて初期化する
  //   （rollover 再実行可）。base は fine grid（kw0=baseKmin 起点・長さ size）で受け、複製して保持
  //   （呼び出し側配列を破壊しない）。forming fine grid と pending tick はゼロにリセットする。
  //   fine grid は base と完全同一（kw0=floor(priceMin/gridW)・size=floor(priceMax/gridW)-kw0+1）で揃え、
  //   base と forming の binning を一致させる（Task A 忠実 binning）。
  init({ baseFine, baseKmin, activeTable, priceMin, priceMax, nBins, gridW, formingStart } = {}) {
    this._nBins = Math.max(1, Number(nBins) || 1);
    this._priceMin = Number(priceMin);
    this._priceMax = Number(priceMax);
    this._gridW = Number(gridW);
    this._formingStart = formingStart;
    this._binw = (this._priceMax - this._priceMin) / this._nBins;
    // fine grid の起点 kw0 と長さ size。baseKmin/baseFine.length を優先（backend と厳密整列）、
    //   欠損時は priceMin/priceMax/gridW から導出（backend と同一定義 floor）。
    this._kw0 = Number.isFinite(Number(baseKmin))
      ? Number(baseKmin)
      : Math.floor(this._priceMin / this._gridW);
    const derivedSize = Math.floor(this._priceMax / this._gridW) - this._kw0 + 1;
    const size = Array.isArray(baseFine) && baseFine.length > 0
      ? baseFine.length
      : Math.max(1, derivedSize);
    this._size = size;
    this._base = new Array(size).fill(0);
    if (Array.isArray(baseFine)) {
      for (let i = 0; i < size; i += 1) {
        this._base[i] = Number(baseFine[i]) || 0;
      }
    }
    this._forming = new Array(size).fill(0);
    this._table = activeTable;
    this._prevSec = null;
    this._prevMid = null;
    this._ready = true;
  }

  // tick を 1 本追加する（O(1)）。直前 pending tick の dwell = activeSeconds(prevSec, sec) を、
  //   prev tick の価格 mid の fine bin k=floor(mid/gridW) へ加算する（表示 bin ではない＝忠実 binning）。
  //   fine grid 範囲外（[kw0, kw0+size)）は捨てる（mp_core fine grid の境界と一致）。追加 tick 自身は
  //   次 tick 未着まで dwell=0。
  addTick(sec, mid) {
    if (!this._ready) {
      return;
    }
    if (this._prevSec !== null) {
      const dwell = activeSeconds(this._prevSec, sec, this._table);
      if (dwell > 0) {
        const off = Math.floor(Number(this._prevMid) / this._gridW) - this._kw0;
        if (off >= 0 && off < this._size) {
          this._forming[off] += dwell;
        }
      }
    }
    this._prevSec = sec;
    this._prevMid = mid;
  }

  // combined fine（base+forming）を表示 bin へ再集計したプロファイルを返す（純・状態を破壊しない）。
  //   fine bin 中心 (kw0+i+0.5)*gridW → 表示 bin disp=clip(floor((center-priceMin)/binw),0,nBins-1) へ
  //   加算する（mp_core.compute_profile の centers_fine→disp 再集計と厳密同型）。応答スキーマは backend
  //   market_profile と同一（bins/poc/va_low/va_high/price_min/price_max/tpo_units/n_bins）。
  snapshot() {
    const n = this._nBins;
    const binw = this._binw;
    // combined fine を表示 bin へ再集計する（binning 一致の要）。
    const tpo = new Array(n).fill(0);
    let sum = 0;
    for (let i = 0; i < this._size; i += 1) {
      const combined = this._base[i] + this._forming[i];
      if (combined === 0) {
        continue;
      }
      const centerFine = (this._kw0 + i + 0.5) * this._gridW;
      let disp = Math.floor((centerFine - this._priceMin) / binw);
      if (disp < 0) {
        disp = 0;
      } else if (disp >= n) {
        disp = n - 1;
      }
      tpo[disp] += combined;
      sum += combined;
    }
    const centers = new Array(n);
    let max = 0;
    let pocIdx = 0;
    for (let i = 0; i < n; i += 1) {
      centers[i] = this._priceMin + (i + 0.5) * binw;
      tpo[i] = Math.round(tpo[i]);
      if (tpo[i] > max) {
        max = tpo[i];
      }
      if (tpo[i] > tpo[pocIdx]) {
        pocIdx = i;
      }
    }
    const tmax = max > 0 ? max : 1;
    const [vaLow, vaHigh] = valueArea(centers, tpo, VA_PCT);
    const bins = new Array(n);
    for (let i = 0; i < n; i += 1) {
      bins[i] = {
        price: round2(centers[i]),
        tpo: tpo[i],
        norm: Math.round((tpo[i] / tmax) * 10000) / 10000,
      };
    }
    return {
      bins,
      poc: round2(centers[pocIdx]),
      va_low: round2(vaLow),
      va_high: round2(vaHigh),
      price_min: this._priceMin,
      price_max: this._priceMax,
      tpo_units: Math.round(sum),
      n_bins: n,
    };
  }
}

// 価格を小数 2 桁へ丸める（backend compute の round(x, 2) と同値・DRY）。
function round2(x) {
  return Math.round(Number(x) * 100) / 100;
}
