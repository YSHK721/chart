// tickvol_bands_primitive.js — 取引密度が濃い時刻帯のチャートパネル背景を塗る lwc プリミティブ。
//
// 装着規約は既存プリミティブと同一（attached/detached/paneViews/_draw）。`zOrder='bottom'` で
//   系列の下＝背景側に塗るため、ローソク・指標線は原色のまま上に残る（replay_boundary_dim と同じ）。
//
// 入力は「塗る帯の左端バー time / 右端バー time」の配列だけ（どのバーを塗るかの判定は
//   domain/tickvol_bands.js の純ロジックが決める＝本モジュールは座標変換と塗りに徹する）。
//
// x 変換: timeToIndex→logicalToCoordinate を優先する（market_profile_primitive の日スパンと同方式）。
//   timeToCoordinate は可視域外で null を返すため、帯の片端が画面外へ出ると帯ごと消えてしまう。
//   index 経由なら部分可視でも座標が出る。非提供環境（fake/旧 lwc）は timeToCoordinate へ退避。
//
// 色: 本番背景 #131722 に対するアクセント（#2962ff）の低アルファ。半透明にしているのは、
//   リプレイの減光境界プリミティブ（不透明 #090d18・同じ zOrder='bottom'）と重なったとき、
//   どちらの順で描かれても両方の意味が読めるようにするため。
// ★命名規約: build.mjs（A 方式バンドル）は全モジュールの top-level を 1 スコープへ連結するため、
//   トップレベル定数は機能名で前置しないと他モジュールと衝突する（PAIR_DIM_ALPHA の前例）。

import { CHROME_CURRENT } from '../../usecase/chrome_tokens.js';

export class TickvolBandsPrimitive {
  constructor() {
    // 段階 5-E: 帯の色は注入で受ける（canvas は CSS 変数を解決できない）。未注入時の既定だけを
    //   台帳から引くため、テーマなしの見た目は現行と厳密に同一。
    this._fill = CHROME_CURRENT.tickvolBand;
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
    this._ranges = []; // [{from, to}]（バー time・空＝塗らない）
    this._paneView = {
      renderer: () => ({ draw: (target) => this._draw(target) }),
      zOrder: () => 'bottom',
    };
  }

  attached({ chart, series, requestUpdate }) {
    this._chart = chart;
    this._series = series;
    this._requestUpdate = requestUpdate;
  }

  detached() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  // 塗る帯（バー time の閉区間）を設定し再描画要求。空配列で消灯。
  setRanges(ranges) {
    this._ranges = Array.isArray(ranges) ? ranges : [];
    if (typeof this._requestUpdate === 'function') {
      this._requestUpdate();
    }
  }

  // 配信されたクロム色から自分の 1 点を取り込む。全域的（§7.3 LSP）: null・非オブジェクト・
  //   非文字列でも例外を投げず、解釈できない指定は現行値を保つ。
  setChromeColors(slots) {
    if (!slots || typeof slots !== 'object' || typeof slots.tickvolBand !== 'string') {
      return;
    }
    this._fill = slots.tickvolBand;
    if (typeof this._requestUpdate === 'function') {
      this._requestUpdate();
    }
  }

  paneViews() {
    return [this._paneView];
  }

  // バー time → media x（バー中心）。解決できなければ null（＝当該帯は描かない）。
  _x(timeScale, time) {
    if (typeof timeScale.timeToIndex === 'function'
        && typeof timeScale.logicalToCoordinate === 'function') {
      const i = timeScale.timeToIndex(time, false);
      if (i != null) {
        const x = timeScale.logicalToCoordinate(i);
        if (x != null) {
          return x;
        }
      }
    }
    return (typeof timeScale.timeToCoordinate === 'function')
      ? timeScale.timeToCoordinate(time)
      : null;
  }

  _draw(target) {
    if (!this._ranges.length || !this._chart) {
      return;
    }
    const timeScale = this._chart.timeScale && this._chart.timeScale();
    if (!timeScale) {
      return;
    }
    // 端バーのスロット全体を含める（timeToIndex/timeToCoordinate は足の「中心」x を返すため、
    //   半バー分だけ外へ広げないと帯の端の足が半分だけ塗られる）。
    const half = ((typeof timeScale.options === 'function' && timeScale.options().barSpacing) || 0) / 2;
    const spans = [];
    for (const r of this._ranges) {
      const xL = this._x(timeScale, r.from);
      const xR = this._x(timeScale, r.to);
      if (xL == null || xR == null) {
        continue; // 表示データに無い帯は塗らない（0 扱いにしない）。
      }
      spans.push([xL - half, xR + half]);
    }
    if (!spans.length) {
      return;
    }
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      ctx.fillStyle = this._fill;
      for (const [l, r] of spans) {
        const left = Math.max(0, Math.min(scope.bitmapSize.width, l * scope.horizontalPixelRatio));
        const right = Math.max(0, Math.min(scope.bitmapSize.width, r * scope.horizontalPixelRatio));
        if (right <= left) {
          continue; // 可視域外
        }
        ctx.fillRect(left, 0, right - left, scope.bitmapSize.height);
      }
    });
  }
}
