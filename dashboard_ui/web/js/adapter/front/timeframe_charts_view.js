// timeframe_charts_view（adapter/front/timeframe_charts_view.js）— 各時間足チャート一覧の版面。
//
// 設計入力: ISSUE-452 内容 2「各時間足のチャート一覧（8 枚を同一画面に配置）」（依頼者指示
//   2026-08-30 で実装確定・置き場所は /dashboard）。各チャートは当該時間足のローソクの上へ、
//   第 1 表（価格ラダー）の**同じ応答**が持つ当該時間足の水準を価格線として重ねる。
//
// 計算量（CLAUDE.md 絶対命令 §4.1・ISSUE-452 の不変条件）:
//   - 水準は**再計算しない・再取得しない**。描くのは /reach_sheet 応答の rows そのもの
//     （ラダーとチャート一覧で同じ計算を二重に発行しない——ISSUE-452 禁止事項）。
//   - 価格線は**差分適用**する。毎描画で全線を作り直すと、応答が 1 秒周期で来るため
//     「作ってから捨てる」線が毎秒 88 本生まれる（出力は正しいまま＝状態検証では落ちない。
//     ISSUE-450 と同型）。同一水準は触らず、価格だけ動いた線は applyOptions で動かし、
//     消えた線だけ removePriceLine する。charts_paint_complexity.test.js が回数で固定する。
//   - ローソクの取得判定は candle_poller が唯一の持ち主。本 View は渡されたものを描くだけ。
//
// チャートの参照実装は live の chart_bootstrap.js（lightweight-charts v5: createChart ＋
//   addSeries(CandlestickSeries, …)・水準線は series.createPriceLine——chart_renderer.js の
//   horizontal_line と同じ面）。`lwc` は注入（unified_root が live の vendor で
//   `window.LightweightCharts` を公開済み・単体ページ等で無い環境は**文字で掲示**して縮退する）。
//
// DOM は View が生成し所有する（index.html へ直書きしない・overlay_host.js 規約）。
// 発行（HTTP）も時計も持たない——描くだけ（reach_sheet_view.js と同じ理由）。

import { DASHBOARD_TIMEFRAMES } from './timeframes.js';
import { createElementWith } from './dom_element.js';
// 色は heat_scale.js が唯一源（canvas の内側には CSS トークンが届かないため、チャート用の
//   色もそちらで定義する。第 2 定義は heat_scale.test.js が機械的に禁じている）。
import { CHART_COLORS as COLORS } from './heat_scale.js';

/**
 * チャート描画ライブラリとして使えるか（合成根と View の共通判定・唯一源）。
 *
 * 合成根はこれが偽ならローソクの発行系（candle_poller）を**組み立てない**——描けない結果を
 * 取得するのは「作ってから捨てる」型の浪費であり、判定を写しで持つと片方だけ直る。
 */
export function chartsLibUsable(lwc) {
  return !!(lwc && typeof lwc.createChart === 'function');
}

/** 価格線の見た目（破線）。lwc.LineStyle が引けない環境（テストダブル）は v5 の実値 2。 */
function dashedStyleOf(lwc) {
  return lwc && lwc.LineStyle && Number.isFinite(lwc.LineStyle.Dashed) ? lwc.LineStyle.Dashed : 2;
}

/**
 * ローソクを time 昇順・重複なしへ整える（後着優先）。
 *
 * lightweight-charts は「厳密増加する time」を要求する（ISSUE-167: 重複 time が 1 本でも
 * 混じると毎 rAF フレーム throw する）。上流の /candles は昇順を返すが、契約ではなく実装の
 * 現状なので、描く側で不変条件にして守る（candle_feed.js と同じ理由・同じ後着優先）。
 */
function toStrictlyIncreasing(candles) {
  const byTime = new Map();
  for (const candle of Array.isArray(candles) ? candles : []) {
    if (candle && Number.isFinite(Number(candle.time))) {
      byTime.set(Number(candle.time), candle);
    }
  }
  return [...byTime.keys()].sort((a, b) => a - b).map((time) => byTime.get(time));
}

/**
 * 応答 rows から時間足 1 本ぶんの水準を引く。
 *
 * 線の同一性キーは label（＋同名の出現順）。価格をキーへ含めると、水準がティックで動くたび
 * 「別の線」扱いになって作り直しが走る——動いた線は同じ線のまま価格だけ動かすのが差分の意味。
 */
function wantedLinesOf(rows, timeframe) {
  const wanted = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || String(row.timeframe) !== timeframe || !Number.isFinite(Number(row.price))) {
      continue;
    }
    const label = String(row.label ?? '');
    let key = label;
    for (let n = 2; wanted.has(key); n += 1) {
      key = `${label}#${n}`;
    }
    wanted.set(key, { price: Number(row.price), label, reached: Number(row.distance) < 0 });
  }
  return wanted;
}

/**
 * 各時間足チャート一覧の View を作る。
 *
 * @param {object}      opts
 * @param {object}      opts.doc DOM 実装（注入）
 * @param {object|null} opts.lwc lightweight-charts（global LightweightCharts 相当・注入）。
 *                               無い環境ではチャートを出さず、理由を文字で掲示する（無言縮退の禁止）。
 * @returns {{mount: Function, render: Function, setCandles: Function,
 *            setCandleError: Function, unmount: Function}}
 */
export function createTimeframeChartsView({ doc, lwc = null } = {}) {
  let root = null;
  let message = null;
  /** 時間足 → タイルの状態（chart / series / 価格線の差分台帳 / 掲示欄）。 */
  let slots = null;

  const el = (tag, props = {}) => createElementWith(doc, tag, props);
  const chartsUsable = chartsLibUsable(lwc);

  /** 版面（枠・見出し・タイル 8 枚）を組んでホストへ挿す。 */
  function mount(host) {
    if (!doc || typeof doc.createElement !== 'function') {
      return null;
    }
    if (!host || typeof host.appendChild !== 'function') {
      throw new Error('timeframe_charts_view: ホストが渡されていないため版面を配置できない');
    }
    root = el('section', { className: 'dash-charts' });
    message = el('p', { className: 'dash-sheet-message' });
    root.appendChild(message);

    const panel = el('div', { className: 'dash-panel' });
    const head = el('div', { className: 'dash-panel-head' });
    head.appendChild(el('h2', { className: 'dash-sheet-title', textContent: '各時間足チャート' }));
    head.appendChild(el('p', {
      className: 'dash-panel-lead',
      textContent: 'その時間足の水準（価格ラダーと同じ応答）を価格スケール上のラベルで示す。線は引かない。',
    }));
    panel.appendChild(head);

    slots = new Map();
    if (!chartsUsable) {
      // 描けないことを文字で掲示する（§5.2 と同じ規約: 空欄で紛らせない）。
      message.textContent = 'チャート描画ライブラリ（lightweight-charts）が読み込まれていない'
        + 'ため、チャート一覧を表示できません。';
    } else {
      const grid = el('div', { className: 'dash-charts-grid' });
      for (const timeframe of DASHBOARD_TIMEFRAMES) {
        grid.appendChild(buildTile(timeframe));
      }
      panel.appendChild(grid);
    }
    root.appendChild(panel);
    host.appendChild(root);
    return root;
  }

  /** タイル 1 枚（見出し＋チャート容器）を組み、チャートを起こす。 */
  function buildTile(timeframe) {
    const tone = DASHBOARD_TIMEFRAMES.indexOf(timeframe);
    const tile = el('div', { className: 'dash-chart-tile', dataset: { timeframe } });
    const head = el('div', { className: 'dash-chart-head' });
    head.appendChild(el('u', {
      className: `dash-tf-pill dash-tf-r${tone}`,
      textContent: timeframe,
    }));
    const status = el('i', { className: 'dash-chart-status', textContent: '読込中…' });
    head.appendChild(status);
    tile.appendChild(head);

    const canvasHost = el('div', { className: 'dash-chart-canvas' });
    tile.appendChild(canvasHost);

    const chart = lwc.createChart(canvasHost, {
      autoSize: true,
      layout: {
        background: lwc.ColorType
          ? { type: lwc.ColorType.Solid, color: 'transparent' }
          : { color: 'transparent' },
        textColor: COLORS.text,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: COLORS.grid },
        horzLines: { color: COLORS.grid },
      },
      rightPriceScale: { borderColor: COLORS.grid },
      // rightOffset: 最新足とスケールの間にローソク 1 本分の余白（依頼者指示 2026-08-30。
      //   最新足が右端へ張り付くと、スケールの水準ラベルと最新足の対照が窮屈になる）。
      timeScale: {
        borderColor: COLORS.grid, timeVisible: true, secondsVisible: false, rightOffset: 1,
      },
    });
    const series = chart.addSeries(lwc.CandlestickSeries, {
      upColor: COLORS.up,
      downColor: COLORS.down,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
      borderVisible: false,
    });
    slots.set(timeframe, {
      chart, series, status, levelLines: new Map(),
    });
    return tile;
  }

  /** 水準 1 本の印を作る。
   *
   *  **チャートを横断する線は描かない**（依頼者指示 2026-08-30: 過去まで伸びる線が判断を
   *  迷わせる。確認は最新足の側＝価格スケールで行う）。lineVisible:false で線を消し、
   *  axisLabelVisible:true で**ラダーの価格の値をスケール上のラベル**として置く。
   *  色は支持側 / 抵抗側（ラダーの凡例と同じ意味）。文字 title はローソクを覆うため
   *  持ち込まない（v0.7.9 の「現在値」ラベル削除と同じ理由。水準の同定はラダーが担う）。 */
  function createLevelLine(slot, spec) {
    return slot.series.createPriceLine({
      price: spec.price,
      color: spec.reached ? COLORS.up : COLORS.down,
      lineWidth: 1,
      lineStyle: dashedStyleOf(lwc),
      lineVisible: false,
      axisLabelVisible: true,
    });
  }

  /**
   * 時間足 1 本ぶんの水準線を差分適用する。
   *
   * 同じ線は触らない・動いた線は applyOptions・消えた線だけ remove——「発行した線 −
   * 出力に残る線 = 0」を charts_paint_complexity.test.js が数える。
   */
  function reconcileLevels(slot, wanted) {
    for (const [key, entry] of [...slot.levelLines]) {
      if (!wanted.has(key)) {
        slot.series.removePriceLine(entry.line);
        slot.levelLines.delete(key);
      }
    }
    for (const [key, spec] of wanted) {
      const existing = slot.levelLines.get(key);
      if (!existing) {
        slot.levelLines.set(key, { line: createLevelLine(slot, spec), ...spec });
        continue;
      }
      if (existing.price !== spec.price || existing.reached !== spec.reached) {
        existing.line.applyOptions({
          price: spec.price,
          color: spec.reached ? COLORS.up : COLORS.down,
        });
        existing.price = spec.price;
        existing.reached = spec.reached;
      }
    }
  }

  /**
   * 応答 1 件を描く（/reach_sheet の応答＝ラダーと**同じもの**。ここで計算は発行しない）。
   *
   * @param {object} response arch-spec §9 の応答
   */
  function render(response) {
    if (!root) {
      throw new Error('timeframe_charts_view: mount より先に render は呼べない');
    }
    if (!chartsUsable) {
      return;   // 掲示済み（mount 時）。応答の成否に関わらず描く場所が無い。
    }
    if (!response || response.ok !== true) {
      // 失敗の理由は掲示するが、線とローソクは**前回の正常応答のまま残す**（第 1 表と同じ:
      //   壊れた応答で版面を消すと、失敗のたびにチャートが明滅する）。
      message.textContent = response && response.error && response.error.message
        ? response.error.message
        : 'シートを取得できませんでした';
      return;
    }
    message.textContent = '';
    // 現在値の線・ラベルは置かない（依頼者指示 2026-08-30: 全幅の線が判断を迷わせる。
    //   現在値はローソク系列自身の最終値表示とラダーの現在値行が担う）。
    for (const [timeframe, slot] of slots) {
      reconcileLevels(slot, wantedLinesOf(response.rows, timeframe));
    }
  }

  /**
   * ローソクを 1 時間足ぶん流し込む（取得判定は candle_poller・取得は candles_client の責務）。
   *
   * @param {string} timeframe 時間足コード
   * @param {Array}  candles   /candles の応答（{time, open, high, low, close} の列）
   */
  function setCandles(timeframe, candles) {
    if (!slots) {
      return;   // unmount 後の遅延着弾（合成根も enabled で守る・こちらは二重目の安全）。
    }
    const slot = slots.get(String(timeframe));
    if (!slot) {
      if (!chartsUsable) {
        return;   // チャート無し掲示済みの環境では流し込む先が無い（掲示が理由を担う）。
      }
      throw new TypeError(`timeframe_charts_view: 未知の時間足 ${timeframe} へのローソク供給`);
    }
    const cleaned = toStrictlyIncreasing(candles);
    slot.series.setData(cleaned);
    slot.status.textContent = `${cleaned.length} 本`;
    slot.status.className = 'dash-chart-status';
  }

  /** ローソク取得の失敗をタイルへ掲示する（無言の空チャートにしない）。 */
  function setCandleError(timeframe, reason) {
    const slot = slots ? slots.get(String(timeframe)) : null;
    if (!slot) {
      return;
    }
    slot.status.textContent = String(reason ?? 'ローソクを取得できませんでした');
    slot.status.className = 'dash-chart-status dash-chart-status-error';
  }

  /** 版面を畳む（共有の器へ何も残さない・チャート実体も破棄する）。 */
  function unmount() {
    if (slots) {
      for (const slot of slots.values()) {
        if (slot.chart && typeof slot.chart.remove === 'function') {
          slot.chart.remove();
        }
      }
      slots = null;
    }
    if (root && root.parentNode && typeof root.parentNode.removeChild === 'function') {
      root.parentNode.removeChild(root);
    }
    root = null;
    message = null;
  }

  return { mount, render, setCandles, setCandleError, unmount };
}
