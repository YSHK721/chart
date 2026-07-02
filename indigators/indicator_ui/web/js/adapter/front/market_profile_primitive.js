// market_profile_primitive.js — Market Profile の TPO ヒストグラムを描くカスタム ISeriesPrimitive。
//
// 設計入力: pair_primitive_base.js（attach/paneViews/_update ライフサイクルの土台）・
//   pair_lines_primitive.js（v5 描画作法の手本）。CHART_TRADE_MARKERS_DETAILED_DESIGN.md §10。
//   v5 事実: attached({chart,series,requestUpdate})・paneViews()→renderer().draw(target)→
//   target.useBitmapCoordinateSpace(scope=>scope.context 描画)・series.priceToCoordinate（範囲外 null）。
//
// 責務（SRP）: profile（bins/poc/va_low/va_high）を「チャート右側・価格軸整列の横バー＋POC 線＋VA 帯線」
//   として描く _draw に限定する。取得・トグル状態は market_profile_actor.js が持つ。
//   price→y 変換は series.priceToCoordinate のみを使い（既存 primitive と同 API）、lwc への直接依存を持たない。

import { PairPrimitiveBase } from './pair_primitive_base.js';

// バー長の最大割合（チャート幅に対する。右端から左へ伸ばす）と最小可視画素。
const BAR_MAX_FRACTION = 0.28;
const MIN_BAR_PX = 1;
// バー高が算出できない（単一 bin 等）場合の既定画素。
const DEFAULT_BAR_H = 2;
// ヒート配色（試作 prototype_260630-01 heatColor 移植）: norm 0..1 を 青(hue240)→シアン→黄→赤(hue0)。
//   累積(norm)が多い価格帯ほど赤く明るい。POC は最頻(norm≈1)で最濃赤になる。
const HEAT_ALPHA = 0.9;
export function heatColor(norm, alpha = HEAT_ALPHA) {
  const t = Math.max(0, Math.min(1, Number(norm) || 0));
  const hue = 240 * (1 - t);           // 0→青(240) / 1→赤(0)
  const light = 46 + 12 * t;           // 高いほど明るく（低 norm も視認できる明度）
  return `hsla(${Math.round(hue)}, 95%, ${Math.round(light)}%, ${alpha})`;
}
// POC 線（最濃赤）・VA 帯線（灰）の色（試作準拠）。
const C_POC_LINE = '#ff3b3b';
const C_VA_LINE = 'rgba(154, 164, 178, 0.9)';

export class MarketProfileHistogramPrimitive extends PairPrimitiveBase {
  constructor() {
    super([]); // 基底の pairs は未使用（本 primitive は profile を描く）。
    this._profile = null;
    this._visible = false;
  }

  // profile を差し替えて再描画要求（取得成功時）。
  setProfile(profile) {
    this._profile = profile;
    this._update();
  }

  // 表示/非表示を切替えて再描画要求（トグル）。
  setVisible(visible) {
    this._visible = !!visible;
    this._update();
  }

  // 連続 bin の y 距離から一様なバー高を導く（隣接間隔の最小値 × 0.85）。単一/未算出は既定値。
  _barHeight(toY) {
    const ys = this._profile.bins
      .map((b) => toY(b.price))
      .filter((y) => y != null)
      .sort((a, b) => a - b);
    let step = 0;
    for (let i = 1; i < ys.length; i += 1) {
      const d = ys[i] - ys[i - 1];
      if (d > 0 && (step === 0 || d < step)) {
        step = d;
      }
    }
    return step > 0 ? step * 0.85 : DEFAULT_BAR_H;
  }

  _draw(target) {
    // 非表示・profile 未取得・attach 前（座標源なし）は描画しない（防御・後方互換）。
    if (!this._visible || !this._profile || !this._chart || !this._series) {
      return;
    }
    const toY = (price) => this._series.priceToCoordinate(price);
    const { bins, poc, va_low: vaLow, va_high: vaHigh } = this._profile;
    const barH = this._barHeight(toY);

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const width = scope.bitmapSize.width;
      const maxBarPx = width * BAR_MAX_FRACTION;

      // TPO 横バー（右端整列・norm で長さ／色）。範囲外（y=null）の bin はスキップ。
      for (const bin of bins) {
        const y = toY(bin.price);
        if (y == null) {
          continue;
        }
        const barW = Math.max(MIN_BAR_PX, (bin.norm ?? 0) * maxBarPx);
        const x0 = width - barW; // 右端（価格軸側）に整列。
        ctx.save();
        ctx.fillStyle = heatColor(bin.norm ?? 0); // 累積(norm)が多いほど赤（試作ヒート配色）。
        ctx.fillRect(x0, y - barH / 2, barW, barH);
        ctx.restore();
      }

      // POC / VAH / VAL の水平参照線（範囲内の価格のみ）。
      const hline = (price, color) => {
        if (price == null) {
          return;
        }
        const y = toY(price);
        if (y == null) {
          return;
        }
        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
        ctx.restore();
      };
      hline(poc, C_POC_LINE);
      hline(vaHigh, C_VA_LINE);
      hline(vaLow, C_VA_LINE);
    });
  }
}
