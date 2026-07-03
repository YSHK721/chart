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
// sessions（日別プロファイル分割）: 1 セッションの最小列幅(px 相当)。分析できる幅を確保する（試作 SESS_MIN_COL）。
const SESS_MIN_COL = 102;
// sessions 列内の日別 POC 行を白で強調（試作準拠）・列内バーの最小可視画素。
const C_SESS_POC = 'rgba(255,255,255,0.95)';
const SESS_BAR_ALPHA = 0.98;
const SESS_MIN_BAR_PX = 1.5;
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
    // sessions（日別プロファイル分割）: 各営業日の [{date,tpo[]}]。null=通常モード（累積プロファイル）。
    //   non-null で sessions モード＝各営業日の列を描き、通常の累積バー・POC/VA 線は描かない（試作準拠）。
    this._sessions = null;
    // sessions_total（キャップ前の実日数）: 注記「直近N/全M日」の M。null=未提供＝受信長へフォールバック。
    //   controller がキャップ後の直近 60 日ぶんだけ返すため、受信 sessions.length では実日数を表せない。
    this._sessionsTotal = null;
  }

  // sessions（日別プロファイル分割）を設定して再描画要求。null で通常モード（累積プロファイル）へ復帰。
  //   移植元 prototype_260630-01 drawSessions。sessions[{date,tpo[]}] は backend の応答トップレベル由来。
  //   total（キャップ前の実日数・任意）は注記「直近N/全M日」の M に使う（未提供時は受信長フォールバック）。
  setSessions(sessions, total = null) {
    this._sessions = Array.isArray(sessions) ? sessions : null;
    this._sessionsTotal = total;
    this._update();
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

  // sessions 描画（移植元 prototype_260630-01 drawSessions）: チャート幅から nFit=floor(width/SESS_MIN_COL)
  //   を求め、直近 nFit 日だけを**全幅に等間隔タイル**（cx=i*colW・試作 L204-210）で描く。
  //   時刻座標(timeToCoordinate)には置かない＝ズームに依存せず常に分析できる列幅を確保し、
  //   どの日かは列上部の日付ラベル(MM-DD)で示す（試作準拠）。列は交互背景で区切り、
  //   列内は日内 max で正規化した横ヒストグラム（heatColor 再利用）・日別 POC 行を白で強調。
  //   直近 nFit < 全日数のときは左下に「直近N/全M日」注記。累積 POC/VA 線・通常バーは描かない。
  _drawSessions(ctx, scope, toY, barH) {
    const all = this._sessions;
    if (!all.length) {
      return;
    }
    const width = scope.bitmapSize.width;
    const height = scope.bitmapSize.height;
    const bins = this._profile.bins;
    // nFit: 列幅 >= SESS_MIN_COL を確保できる日数（直近優先）。1 未満にはしない。
    const nFit = Math.max(1, Math.floor(width / SESS_MIN_COL));
    const ss = all.slice(Math.max(0, all.length - nFit)); // 幅確保のため直近 nFit 日。
    const colW = width / ss.length;                       // 各列の割当幅（全幅タイル）。
    ctx.save();
    ctx.font = '10px system-ui';
    ctx.textBaseline = 'top';
    for (let i = 0; i < ss.length; i += 1) {
      const arr = ss[i].tpo || [];
      const left = i * colW; // 全幅に等間隔タイル（試作 cx = i*colW）。
      // 列を交互背景で区別（試作準拠）。
      ctx.fillStyle = i % 2 ? 'rgba(255,255,255,.05)' : 'rgba(255,255,255,.015)';
      ctx.fillRect(left, 0, colW, height);
      // 日内 max（正規化基準）と日別 POC（最頻 bin index）を求める。
      let dmax = 0;
      let pocj = -1;
      for (let j = 0; j < arr.length; j += 1) {
        if (arr[j] > dmax) {
          dmax = arr[j];
          pocj = j;
        }
      }
      if (dmax > 0) {
        for (let j = 0; j < arr.length; j += 1) {
          const v = arr[j];
          if (!v) {
            continue;
          }
          const bin = bins[j];
          if (!bin) {
            continue;
          }
          const y = toY(bin.price);
          if (y == null) {
            continue; // 範囲外価格はスキップ。
          }
          const w = Math.max(SESS_MIN_BAR_PX, (v / dmax) * (colW - 4));
          // 日別 POC 行は白で強調、それ以外は日内正規化のヒート配色。
          ctx.fillStyle = (j === pocj) ? C_SESS_POC : heatColor(v / dmax, SESS_BAR_ALPHA);
          ctx.fillRect(left + 2, y - barH / 2, w, barH);
        }
      }
      // 列上部に日付（MM-DD・試作準拠）。
      ctx.fillStyle = 'rgba(154,164,178,.6)';
      ctx.fillText((ss[i].date || '').slice(5), left + 3, 4);
    }
    // 直近 nFit < 全日数なら左下に注記（試作準拠）。M はキャップ前の実日数（total）を優先し、
    //   未提供時は受信長（all.length）へフォールバックする（キャップ後 60 の誤読を防ぐ・修正1）。
    const totalDays = this._sessionsTotal ?? all.length;
    if (ss.length < totalDays) {
      ctx.fillStyle = 'rgba(154,164,178,.8)';
      ctx.font = '11px system-ui';
      ctx.fillText(`直近${ss.length}/全${totalDays}日`, 6, height - 16);
    }
    ctx.restore();
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

      // sessions モード（日別プロファイル分割）: 各営業日の列を描き、通常の累積バー・POC/VA 線は描かない。
      if (this._sessions) {
        this._drawSessions(ctx, scope, toY, barH);
        return;
      }

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

      // リプレイ時点 T の縦線（移植元 prototype_260630-01/js/app.js L152-158）。
      //   ★スナップショット（当時トリム）ON 時は描かない：ローソクが T までトリムされ T＝右端の
      //     トリム境界に来るため、縦線は不要（プロト準拠・コメント「当時表示OFF時のみ。ON時はTが
      //     右端なので不要」）。非スナップショット（リプレイのみ）のとき timeToCoordinate(T) で実足に立てる。
      if (this._cursorTime != null && !this._snapshot) {
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
