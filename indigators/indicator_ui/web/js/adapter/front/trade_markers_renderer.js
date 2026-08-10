// trade_markers_renderer.js — 上流 lwc API（createSeriesMarkers）を隔離する adapter。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §3.1、
//   CHART_TRADE_MARKERS_BASIC_DESIGN.md §12.5（C-3 v5 ハンドル方式）、§12.7（M-2 lwc サブセット・M-3 失敗時挙動）。
// chart_renderer.js と同層・同規約（upstream API の唯一の隔離点）。

import { PairLinesPrimitive } from './pair_lines_primitive.js';
import { PAIR_DIM_ALPHA } from './pair_render_constants.js';
import { chromeVar } from './chrome_css_var.js';

// v4 §10.2: 非ハイライト marker の減光色（rgba・低 alpha）。共有定数 PAIR_DIM_ALPHA を参照（単一情報源）。

// "#rrggbb" を rgba(r,g,b,alpha) へ変換する。非 hex はそのまま返す（防御）。
function _withAlpha(color, alpha) {
  if (typeof color !== 'string' || color[0] !== '#' || color.length !== 7) {
    return color;
  }
  const r = parseInt(color.slice(1, 3), 16);
  const g = parseInt(color.slice(3, 5), 16);
  const b = parseInt(color.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export class TradeMarkersRenderer {
  // chart は任意（後方互換）。chart.timeScale().subscribeVisibleTimeRangeChange を購読できる場合のみ
  //   「可視範囲内マーカーのみ描画」モードに入る（§9 Fix v3・左端クランプ列の除去）。購読 API 非提供・
  //   chart 省略時は全件描画フォールバック（現行挙動）。
  //   v4: chart.subscribeCrosshairMove があれば購読し、hoveredObjectId（"t{i}:..."）から
  //   _highlight=i を解析して当該ペア以外を減光する（§10.2・C1）。mainSeries.attachPrimitive が
  //   あれば PairLinesPrimitive を付与して売買ペア線を描く（§10.1）。
  //   v6（§12）: chartRenderer（任意）を受け取ると、hover 中ペア外のローソク足を per-bar 減光させる。
  //   減光/復元は chartRenderer.dimCandlesOutsidePair / restoreCandles を呼ぶ（mainSeries.setData を
  //   直接呼ばない＝upstream 隔離・grep0件規約維持）。chartRenderer 未注入時は全件通常描画フォールバック。
  // ISSUE-026: document / container を DI（既定はブラウザ globalThis.document）。node:test では fakeDoc を注入し
  //   ポップアップの DOM 操作を単体検証する。素の document / getElementById('chart') ハードコードを除去（テスト容易化）。
  constructor({
    lwc, mainSeries, chart = null, chartRenderer = null,
    document = (typeof globalThis !== 'undefined' ? (globalThis.document ?? null) : null),
    container = null,
  }) {
    this._lwc = lwc;
    this._series = mainSeries;
    this._chartRenderer = chartRenderer; // v6: 基準 candles 所有者（dim/restore の委譲先）。
    this._document = document; // ISSUE-026: ポップアップ DOM の生成先（注入可・不在時 null で no-op）。
    this._container = container; // ISSUE-026: ポップアップ配置の基準矩形要素（無ければ #chart を探索）。
    this._handle = null;
    this._all = []; // load した全 lwc マーカー（昇順）。範囲フィルタの元集合。
    this._pairs = []; // v6: load した売買ペア（dim 範囲 [entry_time, exit_time] の参照元）。
    this._range = null; // 直近の可視時間範囲（null=初期・未確定）。
    this._rangeAware = false; // 可視範囲購読が成立したか（成立時のみ範囲フィルタを適用）。
    this._highlight = null; // v4: ホバー中トレード i（null=非ホバー・全件通常）。
    this._primitive = null; // v4: 売買ペア線 primitive（attachPrimitive 非提供時 null）。
    this._candlesDimmed = false; // v6: ローソク減光中か（onCandlesChanged 時の復元要否判定）。
    this._currentTimeframe = null; // 現在の時間足（null=未設定）。setCurrentTimeframe で更新。
    this._targetTimeframe = null; // 該当時間足＝建玉の時間足。load で json.timeframe から取り込む（null=未宣言＝ゲートしない）。

    const sub = chart && chart.timeScale && chart.timeScale();
    if (sub && typeof sub.subscribeVisibleTimeRangeChange === 'function') {
      this._rangeAware = true;
      sub.subscribeVisibleTimeRangeChange((range) => this._applyRange(range));
    }

    // 段階 5-E: クロム色の購読（FR-C13）。ペア線は canvas 描画のため CSS 変数を解決できず、
    //   色を**注入**で受け取る必要がある。配信の所有者は ChartRenderer（保持と配布の単一点）で、
    //   本 class はその袋を primitive へ中継するだけ（色を 1 つも決めない）。
    //   保持しておく理由: primitive は pairs が来て初めて生まれるため、配信が先・生成が後という
    //   順序が普通に起きる。保持しないとその配信は捨てられ、ペア線だけ旧色で残る。
    this._chromeSlots = null;
    if (chartRenderer && typeof chartRenderer.addChromeObserver === 'function') {
      chartRenderer.addChromeObserver((slots) => {
        this._chromeSlots = slots;
        if (this._primitive) {
          this._primitive.setChromeColors(slots);
        }
      });
    }

    // v4・C1: crosshair 購読（マルチキャスト・既存 ChartRenderer 購読と共存）。
    //   副作用非衝突はブラウザ確認（DoD 分離）。node:test は購読登録のみ検証。
    if (chart && typeof chart.subscribeCrosshairMove === 'function') {
      chart.subscribeCrosshairMove((param) => this._onCrosshair(param));
    }
  }

  // 可視範囲変更時のコールバック。範囲を保持し、範囲内（from<=time<=to）のマーカーのみ適用する。
  _applyRange(range) {
    this._range = range;
    this._render();
  }

  // v8・§13: hoveredObjectId（"t{i}:entry"/"t{i}:exit"）から i を解析して _highlight を更新（オンマウス基準）。
  //   不変ガード: ハイライトが不変（next === this._highlight）なら即 return し再描画しない。これで毎クロス
  //   ヘア移動の全再描画（1326 マーカー再設定＋約 1500 本ローソク減光 setData）を排し、イベント間引きに
  //   よる不規則発火を解消する（§13.1-3 への対処）。変化時のみ単一 _render() 経路で再描画する（C2）。
  //   v7 のカーソル画素近接判定は実ブラウザ計測で破綻（§13.1）したため撤去した。
  _onCrosshair(param) {
    const next = this._parseTradeIndex(param && param.hoveredObjectId);
    if (next === this._highlight) {
      return; // 不変ガード：ハイライト不変なら再描画しない。
    }
    this._highlight = next;
    this._render();
    this._updatePopup(param); // ISSUE-026: 取引明細ポップアップ（highlight 状態に同期）。
  }

  // ISSUE-026: 売買ペアの取引明細ステートメントをポップアップ表示する。
  //   highlight 中（グリフ hover 中）は当該ペアの 9 項目を表示、非 highlight では非表示。
  //   既存 v8 hover 経路（hoveredObjectId）に相乗りし、新規購読・新規 fetch を増やさない。
  //   document 不在（node:test/SSR）では no-op（ブラウザ専用 UI）。
  _updatePopup(param) {
    if (!this._document) {
      return; // document 不在（SSR / 未注入）では no-op（後方互換）。
    }
    const el = this._ensurePopup();
    if (!el) {
      return;
    }
    const pair = this._highlight == null
      ? null
      : this._pairs.find((p) => p.i === this._highlight);
    if (!pair) {
      el.style.display = 'none';
      return;
    }
    el.innerHTML = this._popupHtml(pair);
    el.style.display = 'block';
    this._positionPopup(el, param);
  }

  // ポップアップ DOM を遅延生成する（既存なら再利用）。pointer-events:none で hover を妨げない。
  _ensurePopup() {
    if (this._popupEl) {
      return this._popupEl;
    }
    if (!this._document) {
      return null; // document 不在では生成しない（_updatePopup 側で null ガード）。
    }
    const el = this._document.createElement('div');
    el.id = 'trade-detail-popup';
    el.style.cssText = [
      'position:fixed', 'z-index:9999', 'display:none', 'pointer-events:none',
      // 段階 5-E: 枠の地・文字・境界はアプリ UI クロムの既存配線点をそのまま読む（浮遊パネルは
      //   意味としてパネルであり、専用の配線点を作ると同じ意味に 2 つの席ができる）。
      `background:${chromeVar('uiPanel')}`, `color:${chromeVar('uiText')}`,
      `border:1px solid ${chromeVar('uiBorder')}`,
      'border-radius:6px', 'padding:8px 10px', 'font:12px/1.5 system-ui,sans-serif',
      // 影は色ではなく奥行き（台帳の THEME_EXEMPT_LITERALS に登録済の対象外）。
      'box-shadow:0 4px 16px rgba(0,0,0,0.5)', 'min-width:220px',
    ].join(';');
    this._document.body.appendChild(el);
    this._popupEl = el;
    return el;
  }

  // ペアの 9 項目（利益/取引日時/取引時間/取引価格/取引数量/決済日時/決済時間/決済価格/決済数量）を HTML 化する。
  _popupHtml(pair) {
    const profit = pair.profit;
    // 段階 5-E: 利益 / 損失は取引の**成果**（profit / loss）。下のヘッダが使う買い / 売りは
    //   **方向**（bullish / bearish）であり、現行リテラルが同じ #26a69a / #ef5350 でも意味が
    //   違うため別の配線点を読む。ここを 1 つに束ねると「利益は緑・買いは青」が指定できない。
    const profitColor = (typeof profit === 'number' && profit > 0) ? chromeVar('tradeProfit')
      : (typeof profit === 'number' && profit < 0) ? chromeVar('tradeLoss') : chromeVar('uiText');
    const sideLabel = pair.side === 'buy' ? 'BUY' : 'SELL';
    const row = (label, value, color) =>
      `<div style="display:flex;justify-content:space-between;gap:16px">`
      + `<span style="color:${chromeVar('uiTextWeak')}">${label}</span>`
      + `<span style="color:${color || chromeVar('uiText')};font-variant-numeric:tabular-nums">${value}</span></div>`;
    return [
      `<div style="font-weight:600;margin-bottom:4px;color:${pair.side === 'buy' ? chromeVar('tradeSideBuy') : chromeVar('tradeSideSell')}">`
        + `#${pair.i} ${sideLabel}</div>`,
      row('利益', this._fmtNum(profit), profitColor),
      `<div style="border-top:1px solid ${chromeVar('uiBorder')};margin:4px 0"></div>`,
      row('取引日時', this._fmtDate(pair.entry.time)),
      row('取引時間', this._fmtClock(pair.entry.time)),
      row('取引価格', this._fmtNum(pair.entry.price)),
      row('取引数量', this._fmtNum(pair.volume)),
      `<div style="border-top:1px solid ${chromeVar('uiBorder')};margin:4px 0"></div>`,
      row('決済日時', this._fmtDate(pair.exit.time)),
      row('決済時間', this._fmtClock(pair.exit.time)),
      row('決済価格', this._fmtNum(pair.exit.price)),
      row('決済数量', this._fmtNum(pair.volume)),
    ].join('');
  }

  // UNIX 秒を JST（日本時間・UTC+9）の Date オブジェクトへ変換する（ISSUE-026 ユーザー決定）。
  //   実行環境の TZ に依存しないよう +9h オフセットを加えて getUTC* で読む（決定論的）。
  _jst(unixSec) {
    return new Date((unixSec + 9 * 3600) * 1000); // JST = UTC+9。
  }

  // 2 桁ゼロ埋め（_fmtDate / _fmtClock 共通）。
  _pad2(n) {
    return String(n).padStart(2, '0');
  }

  // 日付のみ YYYY/MM/DD（JST）へ整形する（ISSUE-026: 日時と時間を別行に分離）。
  _fmtDate(unixSec) {
    if (typeof unixSec !== 'number') {
      return '-';
    }
    const d = this._jst(unixSec);
    return `${d.getUTCFullYear()}/${this._pad2(d.getUTCMonth() + 1)}/${this._pad2(d.getUTCDate())}`;
  }

  // 時刻のみ HH:MM:SS（JST）へ整形する（ISSUE-026: 日時と時間を別行に分離）。
  _fmtClock(unixSec) {
    if (typeof unixSec !== 'number') {
      return '-';
    }
    const d = this._jst(unixSec);
    return `${this._pad2(d.getUTCHours())}:${this._pad2(d.getUTCMinutes())}:${this._pad2(d.getUTCSeconds())}`;
  }

  // 数値整形（int/double 双方を許容＝余分な末尾 0 を出さない）。
  _fmtNum(v) {
    return typeof v === 'number' ? String(v) : '-';
  }

  // hover 開始位置（グリフに乗った瞬間の param.point）へポップアップを配置し、ビューポート外は
  //   左反転・下端クランプする。**呼び出しは highlight 遷移時のみ**（_onCrosshair の不変ガード前提）＝
  //   同一マーカー hover 中は再配置されない＝マーカー固定（カーソル非追従）。関数単体は point を参照するが追従しない。
  _positionPopup(el, param) {
    const point = param && param.point;
    const container = this._container || (this._document ? this._document.getElementById('chart') : null);
    if (!point || !container) {
      return; // 座標不明時は表示位置を据え置く（display は呼び出し側で block 済み）。
    }
    const rect = container.getBoundingClientRect();
    const pw = el.offsetWidth;
    const ph = el.offsetHeight;
    // window 寸法はブラウザのみ参照（node:test / SSR では未定義のためクランプを行わない）。
    const vw = (typeof window !== 'undefined') ? window.innerWidth : null;
    const vh = (typeof window !== 'undefined') ? window.innerHeight : null;
    let x = rect.left + point.x + 16;
    let y = rect.top + point.y + 16;
    if (vw != null && x + pw > vw) {
      x = rect.left + point.x - pw - 16;
    }
    if (vh != null && y + ph > vh) {
      y = vh - ph - 8;
    }
    if (y < 0) {
      y = 8;
    }
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
  }

  // "t{i}:..." から数値 i を取り出す（不一致は null）。
  _parseTradeIndex(id) {
    if (typeof id !== 'string') {
      return null;
    }
    const m = /^t(\d+):/.exec(id);
    return m ? Number(m[1]) : null;
  }

  // 現在の時間足を受け取り単一 _render 経路で再描画する。該当時間足（建玉の時間足）以外は _render が非表示にする。
  setCurrentTimeframe(timeframe) {
    this._currentTimeframe = timeframe;
    this._render();
  }

  // 現在の可視マーカー集合を upstream へ反映する単一の経路（範囲変更・load・hover 共通＝C2）。
  //   _highlight!=null の時は非ハイライト marker を減光色へ変換し、primitive へ highlight を転送する。
  _render() {
    // load 前（マーカー未保持・ハンドル未生成）は lwc に一切触れない（candles 非干渉・C1 共存）。
    //   crosshair 購読は既存 ChartRenderer と共有されるため、load 前の hover では何もしない。
    if (this._all.length === 0 && !this._handle) {
      return;
    }
    // 該当時間足（建玉の時間足＝_targetTimeframe）以外では売買マークを表示しない。
    //   _targetTimeframe が null（json.timeframe 未宣言）の旧データはゲートせず従来どおり表示（後方互換）。
    if (this._targetTimeframe && this._currentTimeframe && this._currentTimeframe !== this._targetTimeframe) {
      this.setMarkers([]);
      return;
    }
    const visible = this._visibleMarkers();
    const applied = this._highlight == null
      ? visible
      : visible.map((mk) => (this._parseTradeIndex(mk.id) === this._highlight
        ? mk
        : { ...mk, color: _withAlpha(mk.color, PAIR_DIM_ALPHA) }));
    if (this._primitive) {
      this._primitive.setHighlight(this._highlight);
    }
    // v6・§12: ローソク足の per-bar 減光も単一 _render 経路で連動（C2）。
    //   highlight 中ペアの [entry_time, exit_time] 外を ChartRenderer へ減光要求、非 highlight は基準復元。
    this._applyCandleDimming();
    this.setMarkers(applied);
  }

  // v6（§12）: highlight 状態に応じて ChartRenderer へ per-bar 減光/基準復元を委譲する。
  //   highlight 中で一致ペアがあれば [entry_time, exit_time] 外を減光、それ以外は減光中なら復元。
  //   chartRenderer 未注入時は no-op（後方互換・全件通常描画フォールバック）。
  _applyCandleDimming() {
    const cr = this._chartRenderer;
    if (!cr) {
      return;
    }
    const pair = this._highlight == null
      ? null
      : this._pairs.find((p) => p.i === this._highlight);
    if (pair && typeof cr.dimCandlesOutsidePair === 'function') {
      cr.dimCandlesOutsidePair({ from: pair.entry.time, to: pair.exit.time });
      this._candlesDimmed = true;
    } else if (this._candlesDimmed && typeof cr.restoreCandles === 'function') {
      cr.restoreCandles();
      this._candlesDimmed = false;
    }
  }

  // v6（§12・必須条件2）: ChartRenderer 起点の candle 変更通知。hover 中（減光中）なら highlight を
  //   解除し基準色へ戻してから ChartRenderer 本来の書込みに委ねる（同一 mainSeries への dim版 setData と
  //   timeframe/live setData の二重書込み競合を回避）。非ホバー中は何もしない（不要な復元を発火しない）。
  onCandlesChanged() {
    if (this._highlight == null && !this._candlesDimmed) {
      return; // 非ホバー・非減光なら ChartRenderer 本来の書込みに委ねる（二重書込みしない）。
    }
    this._highlight = null;
    this._render(); // highlight 解除 → marker 通常色復帰 ＋ _applyCandleDimming で基準復元。
    this._updatePopup(null); // ISSUE-026: candle 変更で highlight 解除時はポップアップも閉じる。
  }

  // _rangeAware 時は _range で絞った集合、それ以外（フォールバック）は全件を返す。
  //   range が null（初期未確定）の場合は空（左端クランプ列を出さない）。
  _visibleMarkers() {
    if (!this._rangeAware) {
      return this._all;
    }
    const r = this._range;
    if (!r) {
      return [];
    }
    return this._all.filter((m) => r.from <= m.time && m.time <= r.to);
  }

  // lwcMarkers: [{time,position,shape,color,id}]（昇順）。§14・ISSUE-025 で text（価格ラベル）は load 時に除外。
  //   初回は createSeriesMarkers でハンドル生成、以降はハンドルへ setMarkers（v5・C-3）。
  setMarkers(lwcMarkers) {
    if (!this._handle) {
      this._handle = this._lwc.createSeriesMarkers(this._series, lwcMarkers);
    } else {
      this._handle.setMarkers(lwcMarkers);
    }
  }

  // v4・§10.1: 売買ペア線 primitive を mainSeries へ付与する。attachPrimitive 非提供（旧 API）の
  //   series では skip（後方互換・throw しない）。再 load 時は既存 primitive へ pairs を差し替える。
  //   v6・§12: ローソク減光は ChartRenderer の per-bar 着色（dimCandlesOutsidePair）で行うため、
  //   v5 の dimming オーバーレイ primitive（PairDimPrimitive）は付与しない（廃止）。pairs は
  //   _pairs に保持し、_applyCandleDimming が減光範囲 [entry_time, exit_time] の参照元にする。
  _attachPairLines(pairs) {
    this._pairs = pairs || [];
    const canAttach = this._series && typeof this._series.attachPrimitive === 'function';
    if (this._primitive) {
      this._primitive.setPairs(this._pairs);
    } else if (canAttach) {
      this._primitive = new PairLinesPrimitive(this._pairs);
      // 生成時点で既に配信済みの色があれば適用する（配信が先・生成が後でも古い色を残さない）。
      //   未配信（null）なら primitive 自身の既定＝現行リテラルのまま。
      if (this._chromeSlots) {
        this._primitive.setChromeColors(this._chromeSlots);
      }
      this._series.attachPrimitive(this._primitive);
    }
  }

  // 既存マーカーを空配列で除去（ハンドル未生成時は no-op）。
  clear() {
    if (this._handle) {
      this._handle.setMarkers([]);
    }
  }

  // JSON を取得し lwc サブセットのみ抽出して付与する。失敗は warn + 0 件（candles 非干渉＝M-3）。
  async load(url, fetchFn = fetch) {
    try {
      const res = await fetchFn(url);
      if (!res.ok) {
        console.warn(`[trade-markers] fetch ${res.status}`);
        return 0;
      }
      const json = await res.json();
      // lwc サブセットのみ抽出（M-2）。§14・ISSUE-025: text（価格ラベル）を除外する。
      //   text を外すと lwc marker のヒット領域が矢印/円グリフのみに縮小し、価格ラベル領域の hover では
      //   hoveredObjectId が立たない（＝減光が発火しない）。価格ラベル自体も非表示になる（ユーザー要件）。
      this._targetTimeframe = json.timeframe ?? null; // 該当時間足＝建玉の時間足（未宣言は null＝ゲートしない）。
      const lwc = (json.markers || []).map((m) => {
        const { text, ...rest } = m.lwc || {};
        return rest;
      });
      this._all = lwc; // 全件保持（範囲フィルタの元集合・§9）。
      this._attachPairLines(json.pairs || []); // v4: 売買ペア線 primitive（§10.1）。
      this._render(); // 範囲確定済みなら範囲内のみ、フォールバックは全件。
      if (json.count != null) {
        console.info(`[trade-markers] ${json.count} markers`); // H-4 明示
      }
      return lwc.length;
    } catch (e) {
      console.warn('[trade-markers] load failed', e);
      return 0;
    }
  }
}
