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
import { createTimeframeChartsView, chartsLibUsable } from './timeframe_charts_view.js';
import { createReachSheetClient, deriveApiPrefix } from './reach_sheet_client.js';
import { createCandlesClient } from './candles_client.js';
import { createLiveTicksFeed } from './live_ticks_client.js';
import { readInstanceBundle, DASHBOARD_TIMEFRAMES } from './template_binding_reader.js';
import { TIMEFRAME_REFRESH_MS } from './timeframes.js';
import { createSheetPoller } from '../../usecase/sheet_poller.js';
import { createCandlePoller } from '../../usecase/candle_poller.js';

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

/** ローソクの供給元（live core の /candles・T-10: live と同一データセット）。dashboard core は
 *  配信面を複製しない（ISSUE-348 と同型の取り違えを作らない）。period_presets と同じ
 *  「live から借りる」規約であり、単体起動（live 不在）では各タイルへ理由が掲示される。 */
const CANDLES_API_PREFIX = '/live';

/** チャート一覧のローソク本数（末尾から）。水準の照合ではなく文脈の表示が目的なので、
 *  タイル幅で読める程度に留める（増やすほど live core の I/O を 8 面ぶん引く）。 */
const CANDLE_LIMIT = 180;

/** なめらか tick 再生の唯一の実装（live フロントの LiveTickPlayer・依頼者指示 2026-08-31
 *  「ライブチャート仕様に合わせて滑らかに再生」）。再生機構（12 秒固定遅延・100ms 粒度・
 *  カーソル増分・clockOffset）はこの参照実装が正であり、写しを持たず実行時 import で借りる
 *  （period_presets / candles と同じ規約）。取得できない環境（単体テスト・live 停止）では
 *  従来どおり 1s 応答の価格表示に縮退する（失敗容認・現在値が消えることはない）。 */
const LIVE_TICK_PLAYER_PATH = '/live/js/adapter/front/live_tick_player.js';

/** 第 2 表のなめらか再生（依頼者指示 2026-08-31）で `/live_ticks` の tails に使う窓長。
 *  規約は `/compute` と同一（表示範囲＝計算足の本数・付けないとサーバ 1 ステップの費用が
 *  全件に比例する）。値はライブチャートが常用する表示範囲と同じ 1,500 本
 *  （オシレータの窓 window_n=500 とウォームアップを覆う）。 */
const OSC_TAILS_LIMIT = 1500;

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
 * @param {object}   [opts.lwc]          lightweight-charts（既定は global LightweightCharts。
 *                                       unified_root が live vendor の読込後に dashboard を
 *                                       import するため、統合ページでは既定で解決できる）
 * @param {string}   [opts.candlesApiPrefix] ローソク供給元の prefix（既定は live モード）
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
  loadLiveTickPlayer = () => import(LIVE_TICK_PLAYER_PATH),
  lwc,
  candlesApiPrefix = CANDLES_API_PREFIX,
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

  const ladderView = createReachSheetView({
    doc, periodAnnotator, now: () => Math.floor(clock() / 1000),
  });
  const oscillatorView = createOscillatorSheetView({ doc, now: () => Math.floor(clock() / 1000) });
  // lwc は注入が無ければ global から解決する（unified_root は live vendor を読み込んでから
  //   dashboard を import する＝統合ページでは必ず居る。無い環境は View が文字で掲示する）。
  const chartLib = lwc !== undefined
    ? lwc
    : (typeof globalThis !== 'undefined' ? globalThis.LightweightCharts ?? null : null);
  const chartsView = createTimeframeChartsView({ doc, lwc: chartLib });
  const client = transport
    ? createReachSheetClient({ fetch: transport, apiPrefix: prefix })
    : null;
  // 描けない環境（lwc 不在＝View が理由を掲示する）ではローソクを**取得しない**。取得だけ
  //   して捨てるのは「作ってから捨てる」型の浪費で、出力検証では落ちない（絶対命令 §4.1）。
  const candlesClient = transport && chartsLibUsable(chartLib)
    ? createCandlesClient({ fetch: transport, apiPrefix: candlesApiPrefix })
    : null;

  let enabled = false;
  let poller = null;
  let candlePoller = null;
  let stopTimer = null;
  /** なめらか tick 再生（live の LiveTickPlayer を借りる）。null＝未稼働（縮退表示）。 */
  let tickPlayer = null;
  /** enable 中フラグ（player の非同期 import が disable 後に着弾したら捨てるための札）。 */
  let tickPlayerWanted = false;
  /** サーバの状態トークン（省リソース段階 2・依頼者承認 2026-08-30）。次要求の known_state に
   *  載せ、素材が不変ならサーバは unchanged の極小応答を返す（シート計算ごと省かれる）。 */
  let sheetState = null;
  /** 直近の完全応答（unchanged 時のチャート差分再描画＝アンカー再試行の材料）。 */
  let lastFullResponse = null;
  /** 直近に描いた内容の鍵（省リソース段階 1: 同一内容なら第 1・第 2 表を作り直さない）。 */
  let lastRenderedKey = null;
  /** 第 2 表のなめらか再生の spec 台帳（唯一源＝完全応答の cells・依頼者指示 2026-08-31）。
   *  セルになった instance **だけ**を申告する（ラダー行の instance へ tails を計算させると
   *  使わない計算を発行することになる・絶対命令 §4.1）。 */
  let oscTailSpecs = [];
  /** instanceId → 流し先（セルの行列と系列名）。specs と同時に組み直す。 */
  const oscTailTargets = new Map();

  /** 完全応答の cells から tails の申告と流し先を組み直す。 */
  function rebuildOscTailSpecs(response) {
    const cells = Array.isArray(response.cells) ? response.cells : [];
    oscTailSpecs = [];
    oscTailTargets.clear();
    for (const cell of cells) {
      const key = cell ? cell.instance_key : null;
      if (!Array.isArray(key) || key.length !== 4 || !cell.value_series) {
        continue;   // 宣言の無いセル（旧応答・積算セル等）は流さない＝従来の 1s 表示のまま。
      }
      const instanceId = key.join('\u0000');
      if (oscTailTargets.has(instanceId)) {
        continue;
      }
      let params;
      try {
        params = JSON.parse(key[2]);   // params_key は json.dumps＝JSON として復元できる（契約）。
      } catch {
        continue;
      }
      if (!params || typeof params !== 'object') {
        continue;
      }
      oscTailSpecs.push({
        instanceId,
        indicatorId: key[0],
        variant: key[1],
        // 計算足はセルの列（ISSUE-274 の MTF override と同じ規約で params.timeframe に載せる）。
        params: { ...params, timeframe: key[3] },
      });
      oscTailTargets.set(instanceId, {
        indicatorId: cell.indicator_id,
        timeframe: cell.timeframe,
        valueSeries: cell.value_series,
      });
    }
  }

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
    // 省リソース段階 2: unchanged＝素材不変。トークンだけ受け取り、DOM は一切触らない
    //   （チャートの差分描画のみ通す＝右端余白アンカーの再試行が枯れないように）。
    if (response && response.ok === true && response.unchanged === true) {
      if (typeof response.state === 'string') {
        sheetState = response.state;
      }
      if (lastFullResponse) {
        chartsView.render(lastFullResponse);
      }
      return;
    }
    if (response && response.ok === true) {
      if (typeof response.state === 'string') {
        sheetState = response.state;
      }
      lastFullResponse = response;
      // 第 2 表のなめらか再生: セルの構成が変わりうるのは完全応答のときだけ。
      rebuildOscTailSpecs(response);
    }
    // 省リソース段階 1: 内容が直前の描画と同一なら第 1・第 2 表を作り直さない
    //   （毎秒の全再構築は内容不変時にはまるごと浪費・依頼者指摘 2026-08-30）。
    //   日付印を鍵へ含める＝到達時刻の「今日/昨日」表記が日替わりで確実に描き直される。
    const key = `${Math.floor(clock() / 86_400_000)}|${JSON.stringify(response)}`;
    if (key !== lastRenderedKey) {
      ladderView.render(response);
      oscillatorView.render(response);
      lastRenderedKey = key;
    }
    // チャート一覧は**同じ応答**で描く（ISSUE-452 禁止事項: 二重発行の不在）。差分適用のみ
    //   なので同一内容では発行 0（charts_paint_complexity で固定済み）。
    chartsView.render(response);
  }

  /** ローソク 1 時間足ぶんの取得と供給（発行するかは candle_poller が決める）。 */
  async function issueCandles(timeframe) {
    const result = await candlesClient.fetchCandles({
      datasetRef: DATASET_REF, timeframe, limit: CANDLE_LIMIT,
    });
    if (!enabled) {
      return result;   // モードを出た後の遅延着弾は捨てる（present と同じ 1 箇所ガード）。
    }
    if (result.ok) {
      chartsView.setCandles(timeframe, result.candles);
    } else {
      chartsView.setCandleError(timeframe, result.error.message);
    }
    return result;
  }

  /** 契機を 1 つ通す（発行するかは sheet_poller / candle_poller が決める）。 */
  async function refresh() {
    if (!enabled || !poller) {
      return null;
    }
    if (candlePoller) {
      candlePoller.tick();
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
        // 省リソース段階 2: 既知トークン。素材が不変ならサーバは unchanged を返す。
        //   bodyKey（同一周期の畳み込み）はこの欄を見ない＝畳み込みは従来どおり。
        known_state: sheetState,
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
    // DOM の並びは版面の読み順（左列: ラダー → オシレーターラダー、右列: チャート 70%・
    //   依頼者指示 2026-08-30 追補）。置き場所そのものは CSS（dashboard.css の
    //   grid-template-areas）が唯一源。
    ladderView.mount(anchor);
    oscillatorView.mount(anchor);
    chartsView.mount(anchor);
    enabled = true;
    // 器を出し直した直後は必ず完全応答が要る（unchanged では空の版面が残る）。
    sheetState = null;
    lastFullResponse = null;
    lastRenderedKey = null;
    oscTailSpecs = [];
    oscTailTargets.clear();

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
    candlePoller = candlesClient
      ? createCandlePoller({
        issue: issueCandles,
        now: clock,
        timeframes: DASHBOARD_TIMEFRAMES,
        refreshMs: TIMEFRAME_REFRESH_MS,
      })
      : null;
    await refresh();
    stopTimer = startTimer(() => { refresh(); }, TICK_INTERVAL_MS);

    // なめらか tick 再生（依頼者指示 2026-08-31: ライブチャート仕様＝12 秒固定遅延・100ms
    //   粒度・全ティック適用）。実装は live の LiveTickPlayer そのもの（再生の規約は写さない）。
    //   renderer には「現在値行のその場書き換え」だけを結線する＝表の構成（並び・距離）は
    //   従来どおり 1s の応答描画が持つ（フロントは数値を再計算しない・arch-spec §9）。
    //   import 失敗（単体テスト・live 停止）は握りつぶし＝従来表示のまま。
    tickPlayerWanted = true;
    if (transport) {
      loadLiveTickPlayer().then((mod) => {
        if (!tickPlayerWanted || tickPlayer !== null
            || !mod || typeof mod.LiveTickPlayer !== 'function') {
          return;
        }
        const feed = createLiveTicksFeed({ fetch: transport, apiPrefix: candlesApiPrefix });
        tickPlayer = new mod.LiveTickPlayer({
          renderer: {
            updateLastCandle: (bar) => {
              if (enabled && bar) {
                ladderView.updateCurrentPrice(bar.close);
              }
            },
          },
          fetchLiveTicks: feed.fetchLiveTicks,
          loadFormingBar: feed.loadFormingBar,
          datasetRef: DATASET_REF,
          getTimeframe: () => CHART_TIMEFRAME,
          // 第 2 表のなめらか再生（依頼者指示 2026-08-31: ライブチャートと同じ更新粒度）。
          //   ISSUE-250 Phase 1 の同梱経路そのもの: poll でセルの instance を申告し、
          //   各 tick 時点の末尾値（サーバ計算）を tick 適用と同一同期ブロックで流す。
          //   フロントは数値を再計算しない（値の唯一源はサーバの tails）。
          getComputeSpecs: () => oscTailSpecs,
          getLimit: () => OSC_TAILS_LIMIT,
          applyFormingTails: (tails) => {
            if (!enabled || !tails) {
              return;
            }
            for (const [instanceId, seriesMap] of Object.entries(tails)) {
              const target = oscTailTargets.get(instanceId);
              if (!target || !seriesMap) {
                continue;
              }
              const value = seriesMap[target.valueSeries];
              if (value === undefined) {
                continue;
              }
              oscillatorView.updateCellValue(target.indicatorId, target.timeframe, value);
            }
          },
          // タイマは**必ずラップして**渡す。player は `this._setInterval(...)` とメソッド形で
          //   呼ぶため、素の globalThis.setInterval を渡すと this が Window でなくなり
          //   "Illegal invocation" で start が黙って死ぬ（実測 2026-08-31。live 側は bootstrap が
          //   バインド済みを注入しているため無症状＝既定に頼れない）。
          setInterval: (...args) => globalThis.setInterval(...args),
          clearInterval: (...args) => globalThis.clearInterval(...args),
        });
        tickPlayer.start();
      }).catch(() => {});
    }
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
    if (candlePoller) {
      candlePoller.stop();
      candlePoller = null;
    }
    if (typeof stopTimer === 'function') {
      stopTimer();
      stopTimer = null;
    }
    tickPlayerWanted = false;
    if (tickPlayer) {
      tickPlayer.stop();
      tickPlayer = null;
    }
    ladderView.unmount();
    oscillatorView.unmount();
    chartsView.unmount();
    sheetHost.unmount();
  }

  return { enable, disable, refresh };
}
