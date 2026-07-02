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
// スナップショット時の累積バー減光アルファ（試作 prototype_260630-01 DIM_ALPHA=0.30）と当日強調アルファ。
const DIM_ALPHA = 0.30;
const TODAY_ALPHA = 0.98;
// POC 線（最濃赤）・VA 帯線（灰）の色（試作準拠）。
const C_POC_LINE = '#ff3b3b';
const C_VA_LINE = 'rgba(154, 164, 178, 0.9)';
// リプレイ時点 T の縦線色（試作 prototype_260630-01 の遡り縦線準拠・視認しやすい水色）。
const C_CURSOR_LINE = 'rgba(120, 190, 255, 0.9)';

export class MarketProfileHistogramPrimitive extends PairPrimitiveBase {
  constructor() {
    super([]); // 基底の pairs は未使用（本 primitive は profile を描く）。
    this._profile = null;
    this._visible = false;
    // リプレイ時点 T（UNIX 秒）の縦線。null=非描画（replay OFF）。移植元 prototype_260630-01。
    this._cursorTime = null;
    // 増分2 スナップショット: true で累積バーを減光（DIM_ALPHA）＋ today[] を当日内スケールで明るく重畳。
    //   既定 false（明るい累積バー＝従来描画）。移植元 prototype_260630-01 drawComposite（showToday）。
    this._snapshot = false;
  }

  // 増分2: スナップショット表示（累積減光＋当日強調）を切替えて再描画要求。false で従来の明るい累積へ復帰。
  setSnapshot(on) {
    this._snapshot = !!on;
    this._update();
  }

  // profile を差し替えて再描画要求（取得成功時）。
  setProfile(profile) {
    this._profile = profile;
    this._update();
  }

  // リプレイ時点 T（UNIX 秒）を設定して再描画要求。null で縦線を消す（replay OFF）。
  //   x は attach 済み chart の timeScale().timeToCoordinate(T) で解決する（座標源は基底 _chart）。
  setCursorTime(time) {
    this._cursorTime = time == null ? null : time;
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
    // 増分2 スナップショット: 累積バーを減光し、today[] を当日内スケール（today_max）で明るく重畳する。
    const snapshot = this._snapshot;
    const today = (snapshot && Array.isArray(this._profile.today)) ? this._profile.today : null;
    const todayMax = Number(this._profile.today_max) || 1;
    const cumAlpha = snapshot ? DIM_ALPHA : HEAT_ALPHA; // スナップショット時は累積を減光（当日を際立たせる）。

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const width = scope.bitmapSize.width;
      const maxBarPx = width * BAR_MAX_FRACTION;

      // TPO 横バー（右端整列・norm で長さ／色）。範囲外（y=null）の bin はスキップ。
      for (let i = 0; i < bins.length; i += 1) {
        const bin = bins[i];
        const y = toY(bin.price);
        if (y == null) {
          continue;
        }
        const barW = Math.max(MIN_BAR_PX, (bin.norm ?? 0) * maxBarPx);
        const x0 = width - barW; // 右端（価格軸側）に整列。
        ctx.save();
        ctx.fillStyle = heatColor(bin.norm ?? 0, cumAlpha); // 累積(norm)が多いほど赤（スナップショット時は減光）。
        ctx.fillRect(x0, y - barH / 2, barW, barH);
        ctx.restore();
        // 当日ぶん（today>0）を当日内スケールで明るく重畳（スナップショット ON かつ today[] 有り時のみ）。
        if (today && today[i] > 0) {
          const tn = today[i] / todayMax;
          const tw = Math.max(MIN_BAR_PX, tn * maxBarPx);
          ctx.save();
          ctx.fillStyle = heatColor(tn, TODAY_ALPHA);
          ctx.fillRect(width - tw, y - barH / 2, tw, barH);
          ctx.restore();
        }
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

      // リプレイ時点 T の縦線（replay 中のみ・移植元 prototype_260630-01 の遡り縦線）。
      //   x = timeScale().timeToCoordinate(T)。範囲外（null）はスキップ。上下（y=0..height）に伸ばす。
      if (this._cursorTime != null) {
        const ts = this._chart.timeScale && this._chart.timeScale();
        const x = ts && typeof ts.timeToCoordinate === 'function'
          ? ts.timeToCoordinate(this._cursorTime)
          : null;
        if (x != null) {
          const height = scope.bitmapSize.height;
          ctx.save();
          ctx.beginPath();
          ctx.strokeStyle = C_CURSOR_LINE;
          ctx.lineWidth = 1;
          ctx.moveTo(x, 0);
          ctx.lineTo(x, height);
          ctx.stroke();
          ctx.restore();
        }
      }
    });
  }
}
