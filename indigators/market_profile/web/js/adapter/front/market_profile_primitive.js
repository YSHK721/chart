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

// 'YYYY-MM-DD'（sessions の date）→ UNIX 秒（UTC 深夜）。timeToCoordinate へ渡し日付を時間軸に対応づける。
function dateToUnix(dateStr) {
  const parts = String(dateStr).split('-');
  const y = Number(parts[0]);
  const m = Number(parts[1]);
  const d = Number(parts[2]);
  if (!(y > 0) || !(m > 0) || !(d > 0)) {
    return NaN;
  }
  return Date.UTC(y, m - 1, d) / 1000;
}

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
// sessions 各列の OHLC 可視化（ユーザー選択「方向ティント＋終値線」）。
//   列全体を当日方向で薄くティント（陽線=薄緑/陰線=薄赤）し、終値にのみ太い横線（緑/赤）を引く。
//   ヒストグラムの形は保ちつつ、上げ下げが一目で分かりポイント（終値）が際立つ。
const C_SESS_TINT_UP = 'rgba(38, 166, 154, 0.12)';   // 陽線日の列ティント（薄緑）
const C_SESS_TINT_DOWN = 'rgba(239, 83, 80, 0.12)';  // 陰線日の列ティント（薄赤）
// 終値線は控えめに（方向はティントで分かるので、終値は細い目印程度）。1px・やや軟らかい緑/赤。
const C_OHLC_UP = 'rgba(38, 166, 154, 0.8)';         // 終値線（上げ・軟緑）
const C_OHLC_DOWN = 'rgba(239, 83, 80, 0.8)';        // 終値線（下げ・軟赤）
const OHLC_CLOSE_LW = 1;                             // 終値線の太さ（控えめ）
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
    // sessions（日別プロファイル分割）: 各営業日の [{date,tpo[],open,high,low,close,poc,va_low,va_high}]。
    //   null=通常モード（累積プロファイル）。non-null で sessions モード＝各営業日の列を時間軸連動で描く。
    this._sessions = null;
    // sessions タイルの列幅キャッシュ（隣接間隔から算出。可視 <2 のフォールバック用）。
    this._lastSessColW = null;
    // 時間足毎profile列（tf-period・最小価格単位）: [{time, levels:[[price,count]...], poc, ...}]。
    //   null=非適用。non-null で各周期の min-unit 列を時間軸連動で描く（sessions の tf 一般化）。
    this._tfPeriods = null;
    this._tfUnit = null;
    this._lastTfColW = null;
  }

  // sessions（日別プロファイル分割）を設定して再描画要求。null で通常モード（累積プロファイル）へ復帰。
  //   sessions[{date,tpo[],(OHLC),(poc/va)}] は actor が backend 応答＋candle から組み立てたビュー。
  setSessions(sessions) {
    this._sessions = Array.isArray(sessions) ? sessions : null;
    this._update();
  }

  // 時間足毎profile列（tf-period・最小価格単位）を設定して再描画要求。null で非適用へ復帰。
  //   columns[{time, levels:[[price,count]...], poc}] は jitter buffer 経由の可視窓ぶん。unit=最小価格単位。
  setTfPeriods(columns, unit) {
    this._tfPeriods = Array.isArray(columns) && columns.length ? columns : null;
    this._tfUnit = Number.isFinite(unit) ? unit : null;
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

  // sessions 描画: 各営業日プロファイルを**その日の時間座標**（timeToCoordinate(date)）に配置する。
  //   ユーザー選択で試作の全幅画面固定タイルから変更＝横ドラッグ/ズームでタイルもチャートと連動し、
  //   スクロールで過去のセッションをたどれる。列幅は隣接セッションの中央間隔（=barSpacing・daily は
  //   連続バー）から求め（<2 可視時は直前値へフォールバック）、視野外（x=null）はスキップ（カリング）。
  //   列内は日内 max で正規化した横ヒストグラム（heatColor）・日別 POC 行を白。累積 POC/VA・通常バーは描かない。
  //   lwc への到達は series.priceToCoordinate（toY）と timeScale().timeToCoordinate のみ（cursor 線と同じ作法）。
  _drawSessions(ctx, scope, toY, barH) {
    const all = this._sessions;
    if (!all.length) {
      return;
    }
    const height = scope.bitmapSize.height;
    const bins = this._profile.bins;
    const ts = this._chart.timeScale && this._chart.timeScale();
    if (!ts || typeof ts.timeToCoordinate !== 'function') {
      return;
    }
    // 各セッション中心 x（timeToCoordinate。範囲外は null＝カリング）。
    const xs = all.map((s) => {
      const c = ts.timeToCoordinate(dateToUnix(s.date));
      return c == null ? null : c;
    });
    // 列幅 = 隣接セッション中央間隔の中央値（=1バー幅）。<2 可視時は直前値/既定。
    const gaps = [];
    for (let i = 1; i < xs.length; i += 1) {
      if (xs[i] != null && xs[i - 1] != null) {
        const g = Math.abs(xs[i] - xs[i - 1]);
        if (g > 0) {
          gaps.push(g);
        }
      }
    }
    gaps.sort((a, b) => a - b);
    const colW = gaps.length ? gaps[Math.floor(gaps.length / 2)] : (this._lastSessColW || 18);
    this._lastSessColW = colW;
    const tileW = Math.max(3, colW * 0.85);
    ctx.save();
    ctx.font = '10px system-ui';
    ctx.textBaseline = 'top';
    for (let i = 0; i < all.length; i += 1) {
      const cx = xs[i];
      if (cx == null) {
        continue; // 視野外＝スクロールで可視化。
      }
      const left = cx - tileW / 2;
      const s = all[i];
      const arr = s.tpo || [];
      const hasOhlc = s.open != null && s.close != null;
      const hasRange = s.high != null && s.low != null;
      // 列背景: OHLC 有れば**高安レンジ（toY(high)〜toY(low)）だけ**を当日方向でティント
      //   （陽=薄緑/陰=薄赤）。当日値幅を色付きバンドで示し、方向も一目で分かる。
      //   OHLC 無しは従来の交互背景（後方互換・列区別）。
      if (hasOhlc && hasRange) {
        const yHigh = toY(s.high);
        const yLow = toY(s.low);
        if (yHigh != null && yLow != null) {
          const top = Math.min(yHigh, yLow);
          const bot = Math.max(yHigh, yLow);
          ctx.fillStyle = s.close >= s.open ? C_SESS_TINT_UP : C_SESS_TINT_DOWN;
          ctx.fillRect(left, top, tileW, Math.max(1, bot - top));
        }
      } else {
        ctx.fillStyle = i % 2 ? 'rgba(255,255,255,.05)' : 'rgba(255,255,255,.015)';
        ctx.fillRect(left, 0, tileW, height);
      }
      // 日内 max（正規化基準）と日別 POC（最頻 bin index）。
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
          const w = Math.max(SESS_MIN_BAR_PX, (v / dmax) * (tileW - 4));
          ctx.fillStyle = (j === pocj) ? C_SESS_POC : heatColor(v / dmax, SESS_BAR_ALPHA);
          ctx.fillRect(left + 2, y - barH / 2, w, barH);
        }
      }
      // 終値線のみ（列幅いっぱい・太線）。上げ=緑/下げ=赤。ヒストの上に重ね当日の終値ポイントを際立たせる。
      //   actor が candle から o/c を付与済みのときのみ描く（未付与＝後方互換で skip）。
      if (hasOhlc) {
        const y = toY(s.close);
        if (y != null) {
          ctx.strokeStyle = s.close >= s.open ? C_OHLC_UP : C_OHLC_DOWN;
          ctx.lineWidth = OHLC_CLOSE_LW;
          ctx.beginPath();
          ctx.moveTo(left, y);
          ctx.lineTo(left + tileW, y);
          ctx.stroke();
        }
      }
      // 列上部に日付（MM-DD）。
      ctx.fillStyle = 'rgba(154,164,178,.6)';
      ctx.fillText((all[i].date || '').slice(5), left + 3, 4);
    }
    ctx.restore();
  }

  // 時間足毎profile列（tf-period・最小価格単位）を描く。各列を周期始端時刻 x に、各占有レベルを価格 y に、
  //   count/最大で長さ・色（heat）を決める。POC は際立たせる。列幅=隣接列間隔の中央値、レベル高=最小単位の
  //   画素距離（最低1px＝ズームアウト時は密なヒートマップ）。視野外列は timeToCoordinate=null でカリング。
  _drawTfPeriods(ctx, scope, toY) {
    const cols = this._tfPeriods;
    if (!cols || !cols.length) return;
    const ts = this._chart.timeScale && this._chart.timeScale();
    if (!ts || typeof ts.timeToCoordinate !== 'function') return;
    const xs = cols.map((c) => { const x = ts.timeToCoordinate(c.time); return x == null ? null : x; });
    const gaps = [];
    for (let i = 1; i < xs.length; i += 1) {
      if (xs[i] != null && xs[i - 1] != null) { const g = Math.abs(xs[i] - xs[i - 1]); if (g > 0) gaps.push(g); }
    }
    gaps.sort((a, b) => a - b);
    const colW = gaps.length ? gaps[Math.floor(gaps.length / 2)] : (this._lastTfColW || 18);
    this._lastTfColW = colW;
    const tileW = Math.max(3, colW * 0.85);
    let lvlH = 1;
    if (this._tfUnit && cols[0] && cols[0].poc != null) {
      const yA = toY(cols[0].poc); const yB = toY(cols[0].poc + this._tfUnit);
      if (yA != null && yB != null) lvlH = Math.max(1, Math.abs(yA - yB));
    }
    ctx.save();
    for (let i = 0; i < cols.length; i += 1) {
      const cx = xs[i];
      if (cx == null) continue; // 視野外＝スクロールで可視化。
      const left = cx - tileW / 2;
      const c = cols[i];
      const levels = c.levels || [];
      let cmax = 1;
      for (let k = 0; k < levels.length; k += 1) { if (levels[k][1] > cmax) cmax = levels[k][1]; }
      const pocPrice = c.poc;
      for (let k = 0; k < levels.length; k += 1) {
        const price = levels[k][0];
        const cnt = levels[k][1];
        const y = toY(price);
        if (y == null) continue;
        const w = Math.max(SESS_MIN_BAR_PX, (cnt / cmax) * (tileW - 2));
        ctx.fillStyle = (pocPrice != null && Math.abs(price - pocPrice) < 1e-9)
          ? C_SESS_POC : heatColor(cnt / cmax, SESS_BAR_ALPHA);
        ctx.fillRect(left + 1, y - lvlH / 2, w, lvlH);
      }
    }
    ctx.restore();
  }

  _draw(target) {
    const toY0 = (price) => this._series && this._series.priceToCoordinate(price);
    // 時間足毎profile列（tf-period・最小価格単位）は _profile 非依存で描く（sessions の tf 一般化）。
    if (this._visible && this._tfPeriods && this._chart && this._series) {
      target.useBitmapCoordinateSpace((scope) => this._drawTfPeriods(scope.context, scope, toY0));
      return;
    }
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
