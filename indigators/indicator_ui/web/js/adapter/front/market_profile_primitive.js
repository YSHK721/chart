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
// 単日フォーカスの Value Area（VA）純関数: 日別 tpo を降順（同値は index 昇順で決定論化）に累積し、
//   総量×va に達するビン集合の中心価格の min/max を {vah, val} で返す（backend _value_area の JS 版・
//   market_profile.py L148-163 と同ロジック）。tpo（各 bin の量）と bins（{price} 配列）を入力に取り、
//   総量 0・空配列・長さ不一致では null（VA 描画/テキストなし）。純関数＝副作用なし・単体テスト可能。
export function sessionValueArea(tpo, bins, va = 0.70) {
  const arr = Array.isArray(tpo) ? tpo : [];
  const bs = Array.isArray(bins) ? bins : [];
  const n = Math.min(arr.length, bs.length);
  let total = 0;
  for (let i = 0; i < n; i += 1) {
    total += Number(arr[i]) || 0;
  }
  if (total <= 0) {
    return null;
  }
  const threshold = total * va;
  // 降順（-tpo）・同値は index 昇順で決定論化（backend order と一致）。
  const order = [];
  for (let i = 0; i < n; i += 1) {
    order.push(i);
  }
  order.sort((a, b) => ((Number(arr[b]) || 0) - (Number(arr[a]) || 0)) || (a - b));
  let cum = 0;
  let vah = -Infinity;
  let val = Infinity;
  for (const i of order) {
    const price = bs[i].price;
    if (price > vah) vah = price;
    if (price < val) val = price;
    cum += Number(arr[i]) || 0;
    if (cum >= threshold) {
      break;
    }
  }
  return { vah, val };
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
// 単日フォーカスの分析メトリクス: POC 水平線（赤細線・試作 POC 色を流用）と VA 帯線（灰破線）の色。
//   テキスト（POC/VAH/VAL/合計/レンジ）は既存注記トーン（10-11px の淡色）で左上に描く。
const C_FOCUS_POC_LINE = C_POC_LINE;            // POC の赤細線（既存 C_POC_LINE を流用）。
const C_FOCUS_VA_LINE = 'rgba(154, 164, 178, 0.7)'; // VA 帯線（灰破線）。
const C_FOCUS_METRIC = 'rgba(220,228,240,.9)';  // メトリクステキスト（既存注記トーン）。
// 数値の千区切り丸め（toLocaleString）。非有限は空文字（テキスト行をスキップさせる）。
function fmtNum(v) {
  return Number.isFinite(v) ? Number(v).toLocaleString() : '';
}
// sessions 列内の日別 POC 行を白で強調（試作準拠）・列内バーの最小可視画素。
const C_SESS_POC = 'rgba(255,255,255,0.95)';
const SESS_BAR_ALPHA = 0.98;
const SESS_MIN_BAR_PX = 1.5;
// 単日拡大の分割レイアウト（本タスク）: 左 FOCUS_PATH_FRACTION（=70%）にその日のティック推移
//   （価格ライン）、右 30% に MP 横ヒストグラム。dayPath が無い場合は従来の全幅ヒストグラム。
const FOCUS_PATH_FRACTION = 0.70;
// MP バー右端の余白（価格軸に密着させない・実機フィードバック。16px→32px へ倍増の指示）。
const FOCUS_MP_RIGHT_PAD = 32;
// ティック推移（左70%）側の余白（左端と MP 領域境目・MP 側と対称の 32px）。
const FOCUS_PATH_PAD = 32;
// その日のティック推移ライン（視認しやすい水色系・1.5px）。
const C_DAY_PATH = 'rgba(120, 190, 255, 0.95)';
const DAY_PATH_WIDTH = 1.5;
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
    // 単日フォーカス（列クリックで拡大）: null=一覧（直近 nFit タイル）／date 文字列=その 1 日を全幅で描画。
    //   フォーカス date が受信 sessions に無ければ通常一覧へフォールバック（防御）。
    this._sessionFocus = null;
    // 単日拡大の左70%パス（その日のティック推移 [{t,p}...]）。null=path 無し＝従来の全幅ヒストグラム。
    //   setSessionFocus(date, dayPath) で設定し、解除/一覧復帰で null へ戻す。
    this._dayPath = null;
    // 直近 _drawSessions で描画した「表示中 ss の date 配列」。ヒットテスト（sessionDateAt）はこの
    //   描画と同一のレイアウト源を使う（DPR>1 で bitmapWidth≠CSS幅でも xRatio=0..1 で index を解決するため）。
    //   一覧非表示（通常モード or focus 中）は空配列＝列ヒットテスト対象なし。
    this._lastSessDates = [];
  }

  // 単日フォーカス（列クリックで拡大）を設定して再描画要求。null で一覧（直近 nFit タイル）へ復帰。
  //   date 文字列を渡すと _drawSessions がその 1 日だけを全幅で描く。受信 sessions に無い date は
  //   通常一覧へフォールバックする（防御）。actor が sessions OFF/detach で null へ戻す。
  setSessionFocus(date, dayPath = null) {
    this._sessionFocus = date == null ? null : date;
    // 単日拡大の左70%パス（その日のティック推移 [{t,p}...]）。null=path 無し＝従来の全幅ヒストグラム。
    //   date=null（一覧復帰）時は path も必ずクリアする。
    this._dayPath = (this._sessionFocus != null && Array.isArray(dayPath) && dayPath.length > 0)
      ? dayPath : null;
    this._update();
  }

  // ヒットテスト（純メソッド）: xRatio（クリックx / コンテナ CSS 幅・0..1）から、直近描画の一覧レイアウト
  //   における列の date を返す。範囲外・一覧非表示（通常モード or focus 中＝_lastSessDates 空）は null。
  //   描画と同じ「表示中 ss の件数」で index=floor(xRatio*count) を解くため DPR に依存しない。
  sessionDateAt(xRatio) {
    const dates = this._lastSessDates;
    const count = dates.length;
    if (!count) {
      return null;
    }
    const r = Number(xRatio);
    if (!(r >= 0) || r >= 1) {
      return null; // 0..1 の範囲外（NaN 含む）は対象外。右端 1.0 も範囲外扱い。
    }
    const idx = Math.floor(r * count);
    return dates[idx] ?? null;
  }

  // 単日フォーカスの価格レンジ（純メソッド）: date の tpo>0 な bin の価格 min/max（±半ビン）を返す。
  //   actor がこれを renderer.setPriceAutoscaleOverride へ渡し、フォーカスした日の価格帯へ
  //   価格軸を自動ズームする（全期間レンジのままだと形が潰れて検証できないため）。
  //   sessions 未受信・date 不在・全ゼロ日は null（呼び出し側でズームなし）。
  sessionPriceRange(date) {
    const all = this._sessions;
    const bins = this._profile && this._profile.bins;
    if (!all || !bins || date == null) {
      return null;
    }
    const entry = all.find((s) => s && s.date === date);
    if (!entry) {
      return null;
    }
    const arr = entry.tpo || [];
    let min = Infinity;
    let max = -Infinity;
    for (let j = 0; j < arr.length; j += 1) {
      if (arr[j] > 0 && bins[j]) {
        const p = bins[j].price;
        if (p < min) min = p;
        if (p > max) max = p;
      }
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      return null; // 全ゼロ日。
    }
    // 半ビンぶん広げる（bin 中心価格 → バーの上下端まで）。単一 bin でも 0 幅にしない。
    const half = bins.length > 1 ? Math.abs(bins[1].price - bins[0].price) / 2 : Math.max(1, max * 0.001);
    return { min: min - half, max: max + half };
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
  // 単日フォーカス描画（列クリックで拡大）: 1 日ぶん（day.tpo）を全幅（colW=width）で描く。
  //   一覧の 1 列と同じ正規化（日内 max）／ヒート配色（heatColor）／日別 POC 行の白強調を用い、
  //   横バーの長さは (v/dmax)*(width-margin) まで伸ばす（形を大きく検証できる）。ヘッダ相当として
  //   列上部に日付を大きめ（12px）で描き、右側に「クリックで一覧へ」の小注記を添える。
  _drawFocusDay(ctx, scope, toY, barH, day) {
    const width = scope.bitmapSize.width;
    const height = scope.bitmapSize.height;
    const bins = this._profile.bins;
    const arr = day.tpo || [];
    ctx.save();
    ctx.font = '10px system-ui';
    ctx.textBaseline = 'top';
    // 日内 max（正規化基準）と日別 POC（最頻 bin index）を求める。
    let dmax = 0;
    let pocj = -1;
    for (let j = 0; j < arr.length; j += 1) {
      if (arr[j] > dmax) {
        dmax = arr[j];
        pocj = j;
      }
    }
    // 分割レイアウト: dayPath があればヒストグラムを右 30% に縮め（x0=width*FOCUS_PATH_FRACTION 起点）、
    //   左 70% にその日のティック推移（価格ライン）を描く。dayPath 無しは従来どおり全幅（x0=4）。
    //   バーの右端は価格軸に密着させず FOCUS_MP_RIGHT_PAD の余白を確保する（視認ストレス低減・実機FB）。
    const path = this._dayPath;
    const split = Array.isArray(path) && path.length > 0;
    const histW = split ? width * (1 - FOCUS_PATH_FRACTION) : (width - 8);
    const x0 = split ? width * FOCUS_PATH_FRACTION : 4; // バー起点 x（右30%左端 or 従来の左端 4）。
    const barMax = Math.max(1, histW - FOCUS_MP_RIGHT_PAD); // バー最大長（右余白ぶん短く）。
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
        const w = Math.max(SESS_MIN_BAR_PX, (v / dmax) * barMax);
        ctx.fillStyle = (j === pocj) ? C_SESS_POC : heatColor(v / dmax, SESS_BAR_ALPHA);
        ctx.fillRect(x0, y - barH / 2, w, barH);
      }
    }
    // 左 70%: その日のティック推移（価格ライン）。x=(t-tMin)/(tMax-tMin)*width*FOCUS_PATH_FRACTION・
    //   y=toY(p)。1 点しか無い/範囲縮退時はライン無し（防御）。範囲外価格（toY→null）はスキップ。
    if (split) {
      this._drawDayPath(ctx, path, toY, width);
    }
    // 分析メトリクス: POC（日別最頻 bin 価格）・VA（VAH/VAL・70% 純関数）・合計（tpo 総量）・レンジ
    //   （tpo>0 の bin 価格 min〜max）。対応する水平線（POC 赤細線・VA 灰破線）も描く。
    const pocPrice = (pocj >= 0 && bins[pocj]) ? bins[pocj].price : null;
    const va = sessionValueArea(arr, bins, 0.70);
    let total = 0;
    let rMin = Infinity;
    let rMax = -Infinity;
    for (let j = 0; j < arr.length; j += 1) {
      const v = arr[j];
      total += Number(v) || 0;
      if (v > 0 && bins[j]) {
        const p = bins[j].price;
        if (p < rMin) rMin = p;
        if (p > rMax) rMax = p;
      }
    }
    // 水平線: POC（赤細線）＋ VAH/VAL（灰破線）。範囲外価格（toY→null）はスキップ。
    //   各ライン上（直上・左端）に「項目名 価格」ラベルを表示する（実機FB: ラインだけでは値が読めない）。
    const hline = (price, color, dashed, label) => {
      if (price == null) {
        return;
      }
      const y = toY(price);
      if (y == null) {
        return;
      }
      ctx.save();
      if (dashed && typeof ctx.setLineDash === 'function') {
        ctx.setLineDash([4, 3]);
      }
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
      if (label) {
        // ライン直上・左端に「項目名 価格」（ライン色・10px）。textBaseline=top なので y-13 で線の上に載る。
        if (typeof ctx.setLineDash === 'function') {
          ctx.setLineDash([]);
        }
        ctx.fillStyle = color;
        ctx.font = '10px system-ui';
        ctx.fillText(`${label} ${fmtNum(price)}`, 6, y - 13);
      }
      ctx.restore();
    };
    hline(pocPrice, C_FOCUS_POC_LINE, false, 'POC');
    if (va) {
      hline(va.vah, C_FOCUS_VA_LINE, true, 'VAH');
      hline(va.val, C_FOCUS_VA_LINE, true, 'VAL');
    }
    // ヘッダ＋分析メトリクスは**右上ブロック（右寄せ）**に描く。左上は crosshair 読み取り欄・凡例の
    //   オーバーレイ（#chart-overlay-tl）と重なって読めないため（実機検証で判明）。
    //   1行目=「クリックで一覧へ」小注記 → 日付（12px）→ メトリクス（11px）を右端から積む。
    ctx.textAlign = 'right';
    ctx.fillStyle = 'rgba(154,164,178,.8)';
    ctx.font = '10px system-ui';
    ctx.fillText('クリックで一覧へ', width - 6, 6);
    ctx.fillStyle = 'rgba(220,228,240,.95)';
    ctx.font = '12px system-ui';
    ctx.fillText(day.date || '', width - 6, 22);
    // 分析メトリクス（既存注記トーン・11px・toLocaleString 丸め）。
    //   合計は profile メタの atom（単位・dwell='tick滞在秒(セッション認識)' 等）を添える（無ければ単位なし）。
    const atom = (this._profile && this._profile.atom != null) ? String(this._profile.atom) : '';
    const lines = [];
    if (pocPrice != null) {
      lines.push(`POC ${fmtNum(pocPrice)}`);
    }
    if (va) {
      lines.push(`VAH ${fmtNum(va.vah)}  VAL ${fmtNum(va.val)}`);
    }
    if (total > 0) {
      lines.push(atom ? `合計 ${fmtNum(total)} ${atom}` : `合計 ${fmtNum(total)}`);
    }
    if (Number.isFinite(rMin) && Number.isFinite(rMax)) {
      lines.push(`レンジ ${fmtNum(rMin)}〜${fmtNum(rMax)}`);
    }
    ctx.fillStyle = C_FOCUS_METRIC;
    ctx.font = '11px system-ui';
    let ty = 40; // 日付（12px・y=22）の下から積む。
    for (const line of lines) {
      ctx.fillText(line, width - 6, ty);
      ty += 14;
    }
    ctx.textAlign = 'left';
    ctx.restore();
  }

  // 単日拡大の左70%: その日のティック推移（価格ライン）を polyline で描く（本タスク）。
  //   x = (t - tMin) / (tMax - tMin) * width * FOCUS_PATH_FRACTION（先頭=0・末尾=右端整列）。
  //   y = toY(p)（既存の価格軸ズーム済レンジで整列）。範囲外価格（toY→null）は線分を分断する。
  //   時間レンジ縮退（tMin==tMax）・有効頂点 2 未満はラインを描かない（防御）。
  _drawDayPath(ctx, path, toY, width) {
    let tMin = Infinity;
    let tMax = -Infinity;
    for (const pt of path) {
      const t = Number(pt.t);
      if (Number.isFinite(t)) {
        if (t < tMin) tMin = t;
        if (t > tMax) tMax = t;
      }
    }
    const span = tMax - tMin;
    if (!(span > 0)) {
      return; // 時間レンジ縮退（全点同時刻等）はラインを描かない。
    }
    // 左端と MP 領域との境目に余白を設ける（実機フィードバック・MP 側の右余白と対称）。
    const left = FOCUS_PATH_PAD;
    const pathW = Math.max(1, width * FOCUS_PATH_FRACTION - FOCUS_PATH_PAD * 2);
    // 範囲外価格（toY→null）で線分を分断できるよう、連続する有効点を run にまとめて stroke する。
    ctx.save();
    ctx.strokeStyle = C_DAY_PATH;
    ctx.lineWidth = DAY_PATH_WIDTH;
    if (typeof ctx.setLineDash === 'function') {
      ctx.setLineDash([]);
    }
    let run = [];
    const flush = () => {
      if (run.length >= 2) {
        ctx.beginPath();
        ctx.moveTo(run[0].x, run[0].y);
        for (let i = 1; i < run.length; i += 1) {
          ctx.lineTo(run[i].x, run[i].y);
        }
        ctx.stroke();
      }
      run = [];
    };
    for (const pt of path) {
      const t = Number(pt.t);
      if (!Number.isFinite(t)) {
        continue;
      }
      const y = toY(Number(pt.p));
      if (y == null) {
        flush(); // 範囲外価格で分断。
        continue;
      }
      const x = left + ((t - tMin) / span) * pathW;
      run.push({ x, y });
    }
    flush();
    ctx.restore();
  }

  _drawSessions(ctx, scope, toY, barH) {
    const all = this._sessions;
    if (!all.length) {
      this._lastSessDates = []; // 一覧非表示＝列ヒットテスト対象なし。
      return;
    }
    const width = scope.bitmapSize.width;
    const height = scope.bitmapSize.height;
    const bins = this._profile.bins;
    // 単日フォーカス（列クリックで拡大）: focus date が受信 sessions にあれば、その 1 日だけを
    //   全幅（colW=width）で描いて早期 return する（一覧ヒットテスト対象は空＝再クリックで解除）。
    //   focus date が受信 sessions に無ければ通常一覧へフォールバック（防御・下へ流す）。
    if (this._sessionFocus != null) {
      const day = all.find((s) => s && s.date === this._sessionFocus);
      if (day) {
        this._lastSessDates = []; // フォーカス中は列ヒットテスト対象なし（再クリックはどこでも解除）。
        this._drawFocusDay(ctx, scope, toY, barH, day);
        return;
      }
    }
    // nFit: 列幅 >= SESS_MIN_COL を確保できる日数（直近優先）。1 未満にはしない。
    const nFit = Math.max(1, Math.floor(width / SESS_MIN_COL));
    const ss = all.slice(Math.max(0, all.length - nFit)); // 幅確保のため直近 nFit 日。
    const colW = width / ss.length;                       // 各列の割当幅（全幅タイル）。
    // ヒットテスト用に「表示中 ss の date 配列」を保持（描画と同一レイアウト源・sessionDateAt が参照）。
    this._lastSessDates = ss.map((s) => (s ? s.date : null));
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
