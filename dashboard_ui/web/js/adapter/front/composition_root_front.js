// composition_root_front（adapter/front/composition_root_front.js）
//   — dashboard 表示層の合成根。統合ページ側の唯一の入口。
//
// 受け口契約（unified_ui 側は実装済み・commit b13fbae・unified_root.js:392-396）:
//
//     const dashboardHandle = await setupDashboardDisplay({
//       doc: document,
//       host: bottomPane.host(),
//       templates: readOnlyStorage(liveStorage),
//     });
//
//   - `host` は **sim と共有する bottomPane の器**である。したがって `disable()` は必ず
//     unmount し、統合ページへ 1 要素も残さない（残すと sim の版面に残骸が混ざる）。
//   - `templates` は live スコープの**読み取り専用** storage（arch-spec T-2）。どのスコープを
//     読むかを決めるのは統合層であり、View は自分でスコープを選ばない。書き込み口は無い。
//   - `{enable, disable}` を返す（sim の `setupSimDisplay` と同形。層は 1 枚の口で受け渡される）。
//
// 本モジュールの責務は**結線だけ**である（SRP）。DOM は各 View が生成し所有し、色は
//   heat_scale.js が、発行判定は sheet_poller.js が、HTTP は reach_sheet_client.js が持つ。
//   ここで要素を作り始めると中央 factory へ育ち、表示系統を足すたびに改変が要る（OCP 違反）。
//
// 計算量（CLAUDE.md 絶対命令 §4.1）: 発行の判定は sheet_poller が唯一の持ち主であり、ここは
//   契機を渡すだけである。描画のたびに発行する経路を作らない（描画は閉形式・§7 の表どおり）。

import { createSheetHost } from './sheet_host.js';
import { createReachSheetView } from './reach_sheet_view.js';
import { createOscillatorSheetView } from './oscillator_sheet_view.js';
import { createReachSheetClient, deriveApiPrefix } from './reach_sheet_client.js';
import { readInstanceBundle, DASHBOARD_TIMEFRAMES } from './template_binding_reader.js';
import { createSheetPoller } from '../../usecase/sheet_poller.js';

/** 素材（arch-spec T-10: live と同一データセット固定）。 */
const DATASET_REF = 'jp225_tick';

/** 表示時間足の基準（第 1 表の chart 追従水準の軸）。列は 8 本すべて出る。 */
const CHART_TIMEFRAME = DASHBOARD_TIMEFRAMES[0];

/** 段 2 の周期（ms）。ティックより粗く刻み、同一周期の重複発行を畳む。 */
const TICK_INTERVAL_MS = 1_000;

/** CSS の置き場所（配信位置から導く＝prefix を書き写さない）。 */
const STYLE_PATH = '/css/dashboard.css';

/** 期間プリセット換算表の唯一源（indicator_ui の period_presets.js）。統合ページでは
 *  live モードの配信パスから実行時に import して借りる（表を写して持たない）。
 *  取得できない環境（単体テスト・live 停止）では注記なし＝本数のみ表示に縮退する。 */
const PERIOD_PRESETS_PATH = '/live/js/usecase/period_presets.js';

/**
 * 統合ページ側の入口。器と 2 つの表を出し、`/reach_sheet` の発行を回す。
 *
 * @param {object}   opts
 * @param {object}   opts.doc            統合ページの DOM（統合層が渡す）
 * @param {object}   opts.host           器を挿す先（bottomPane の器・sim と共有）
 * @param {object}   opts.templates      live スコープの読み取り専用 storage（T-2）
 * @param {Function} [opts.fetch]        fetch 実装（既定はブラウザの fetch）
 * @param {string}   [opts.apiPrefix]    API prefix（既定は配信位置から導く）
 * @param {Function} [opts.now]          時計 ms（既定は Date.now）
 * @param {Function} [opts.schedule]     周期実行の予約（既定はブラウザの setInterval）。
 *                                       戻り値は停止する関数。注入すると検定が実時間を待たない。
 * @param {Function} [opts.barCloseTimeOf] 最新の確定バー時刻を返す（段の切り替えの契機）
 * @param {Function} [opts.loadPeriodPresets] 期間プリセット module の読み込み
 *                                       （既定は PERIOD_PRESETS_PATH の動的 import。検定は fake を注入）
 * @returns {Promise<{enable: Function, disable: Function, refresh: Function}>}
 */
export async function setupDashboardDisplay({
  doc,
  host,
  templates,
  fetch: fetchFn,
  apiPrefix,
  now,
  schedule,
  barCloseTimeOf,
  loadPeriodPresets = () => import(PERIOD_PRESETS_PATH),
} = {}) {
  const prefix = typeof apiPrefix === 'string' ? apiPrefix : deriveApiPrefix(import.meta.url);
  const transport = typeof fetchFn === 'function'
    ? fetchFn
    : (typeof globalThis !== 'undefined' && typeof globalThis.fetch === 'function'
      ? (...args) => globalThis.fetch(...args)
      : null);
  const clock = typeof now === 'function' ? now : () => Date.now();
  // 「いま何本目のバーか」の供給が無い環境では、周期そのものを段の契機にする（段 1 の
  //   撃ち直しは周期に従う）。時計を勝手に作らないための既定であって、隠れた縮退ではない。
  const barClock = typeof barCloseTimeOf === 'function'
    ? barCloseTimeOf
    : () => Math.floor(clock() / 60_000);
  const startTimer = typeof schedule === 'function'
    ? schedule
    : (fn, everyMs) => {
      if (typeof setInterval !== 'function') return () => {};
      const id = setInterval(fn, everyMs);
      return () => clearInterval(id);
    };

  const sheetHost = createSheetHost({ doc, styleHref: `${prefix}${STYLE_PATH}` });

  // 期間セルの暦期間注記（例 '1週'）。換算表の読み込みは非同期・失敗容認で、
  //   読み込み前・失敗時は null（＝本数のみ表示）。次の描画周期（1s）から効き始める。
  let presetsFor = null;
  loadPeriodPresets().then((mod) => {
    presetsFor = mod && typeof mod.presetsFor === 'function' ? mod.presetsFor : null;
  }).catch(() => {});
  const periodAnnotator = (timeframe, bars) => {
    if (!presetsFor || !Number.isFinite(bars)) {
      return null;
    }
    const hit = presetsFor({
      datasetRef: DATASET_REF, timeframe: String(timeframe),
      maxBars: Number.MAX_SAFE_INTEGER,
    }).find((preset) => preset.bars === bars);
    return hit ? hit.label : null;
  };

  const ladderView = createReachSheetView({ doc, periodAnnotator });
  const oscillatorView = createOscillatorSheetView({ doc, now: () => Math.floor(clock() / 1000) });
  const client = transport
    ? createReachSheetClient({ fetch: transport, apiPrefix: prefix })
    : null;

  let enabled = false;
  let poller = null;
  let stopTimer = null;

  /**
   * 応答を両表へ配る（描画は閉形式・ここで計算を発行しない）。
   *
   * 有効でないときは配らない。モードを出た後に**発行中だった応答**が着弾すると、View は
   * 既に unmount されており（`disable()` は host を sim と共有するため必ず畳む）、そこへ
   * 描こうとすると View が throw する。その throw は `issue` の Promise の中で起きるため
   * 誰も catch せず、unhandled rejection になる（周期実行は戻り値を捨てている）。
   * 判定は**ここ 1 箇所**に置く。呼び出し側ごとに書くと足し忘れが生まれる。
   */
  function present(response) {
    if (!enabled) {
      return;
    }
    ladderView.render(response);
    oscillatorView.render(response);
  }

  /** 契機を 1 つ通す（発行するかは sheet_poller が決める）。 */
  async function refresh() {
    if (!enabled || !poller) {
      return null;
    }
    const bundle = readInstanceBundle({ storage: templates });
    if (!bundle.ok) {
      present({ ok: false, error: { type: 'TemplateBindingError', message: bundle.error.message } });
      return null;
    }
    return poller.tick({
      body: {
        dataset_ref: DATASET_REF,
        chart_timeframe: CHART_TIMEFRAME,
        instances: bundle.instances,
      },
      barCloseTime: barClock(),
    });
  }

  /** dashboard モードへ入るときに呼ばれる。器と 2 表を出し、発行を始める。 */
  async function enable() {
    if (enabled) {
      return;
    }
    const anchor = sheetHost.mount(host);   // アンカーが無ければここで落ちる（フェイルクローズ）。
    if (!anchor) {
      return;                                // DOM 非対応環境（描画対象そのものが無い）。
    }
    ladderView.mount(anchor);
    oscillatorView.mount(anchor);
    enabled = true;

    if (!client) {
      present({ ok: false, error: { type: 'TransportUnavailable', message: 'この環境では通信できません' } });
      return;
    }
    poller = createSheetPoller({
      issue: async (request) => {
        const response = await client.fetchSheet(request);
        present(response);
        return response;
      },
      now: clock,
      tickIntervalMs: TICK_INTERVAL_MS,
    });
    await refresh();
    stopTimer = startTimer(() => { refresh(); }, TICK_INTERVAL_MS);
  }

  /** dashboard モードから出るときに呼ばれる。器ごと畳み、発行を止める。 */
  async function disable() {
    if (!enabled) {
      return;
    }
    enabled = false;
    if (poller) {
      poller.stop();
      poller = null;
    }
    if (typeof stopTimer === 'function') {
      stopTimer();
      stopTimer = null;
    }
    ladderView.unmount();
    oscillatorView.unmount();
    sheetHost.unmount();
  }

  return { enable, disable, refresh };
}
