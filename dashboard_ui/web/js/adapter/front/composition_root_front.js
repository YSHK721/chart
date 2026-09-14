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
// 方針の所在（ISSUE-502 段階 4C・F-3 の是正後）:
//   分割前はここが「結線だけ」を名乗りながら 4 つの**方針**を保持しており、表示系統を 1 つ
//   足すのに 4 箇所（生成 / mount / render / unmount）の改変が要った。方針はすべて外へ出し、
//   表示系統は下の `panels` 台帳へ **1 行**宣言すれば足りる形にした:
//     - 何をいつ描き直すか（描画鍵・重複抑止・unchanged・状態トークン）
//         …… usecase/sheet_presenter.js
//     - なめらか再生の申告と流し先（params_key 復元・申告 ID の合成）
//         …… usecase/tail_specs.js
//     - MP 借用の系統（公開面の検査・失敗文言・client/context/poller・契機）
//         …… adapter/front/mp_borrow.js
//     - tick 再生の台 8 本（renderer 結線・タイマのラップ・tails の受け口）
//         …… adapter/front/live_tick_players.js
//
// 計算量（CLAUDE.md 絶対命令 §4.1）: 発行の判定は各 poller が唯一の持ち主であり、ここは
//   契機を渡すだけである。描画のたびに発行する経路を作らない（描画は閉形式・§7 の表どおり）。

import { createSheetHost } from './sheet_host.js';
import { createReachSheetView } from './reach_sheet_view.js';
import { createOscillatorSheetView } from './oscillator_sheet_view.js';
import { createTimeframeChartsView, chartsLibUsable } from './timeframe_charts_view.js';
import { createReachSheetClient, deriveApiPrefix } from './reach_sheet_client.js';
import { createCandlesClient } from './candles_client.js';
import { createMpBorrow } from './mp_borrow.js';
import { createLiveTickPlayers } from './live_tick_players.js';
import { readInstanceBundle, DASHBOARD_TIMEFRAMES } from './template_binding_reader.js';
import { TIMEFRAME_REFRESH_MS } from './timeframes.js';
import { createSheetPoller } from '../../usecase/sheet_poller.js';
import { createCandlePoller } from '../../usecase/candle_poller.js';
import { createSheetPresenter } from '../../usecase/sheet_presenter.js';
import { createTailSpecLedger, tailInstanceIdOf } from '../../usecase/tail_specs.js';
import { mpNormAt } from '../../domain/mp_bin.js';

/**
 * 素材（arch-spec T-10: live と同一データセット）。ISSUE-512 段階 0: 値は台帳 1 箇所
 * （marketdata/dataset_registry.py の DEFAULT_DATASET_REF → 生成物）から読み、ここには書かない。
 */
import { DEFAULT_DATASET_REF } from '../../domain/dataset_default_generated.js';

/** 表示時間足の基準（第 1 表の chart 追従水準の軸）。列は 8 本すべて出る。 */
const CHART_TIMEFRAME = DASHBOARD_TIMEFRAMES[0];

/** 段 2 の周期（ms）。ティックより粗く刻み、同一周期の重複発行を畳む。 */
const TICK_INTERVAL_MS = 1_000;

/** CSS の置き場所（配信位置から導く＝prefix を書き写さない）。 */
const STYLE_PATH = '/css/dashboard.css';

/** live core の公開面（ISSUE-479 Wave2 J-4b）。dashboard が live から借りるものは
 *  すべてこの 1 本の URL から取る。live core の内部階層（usecase/... や adapter/front/...）を
 *  名指すと、live 側の配置換えで dashboard が無言で 404 になる（識別子渡しの動的 import は
 *  import 走査に映らないため、壊れたことが検定にも型にも現れない）。
 *
 *  借りているもの: 期間プリセット換算表（期間 → 本数の唯一源。取得できない環境では注記なし
 *  ＝本数のみ表示に縮退）と、なめらか tick 再生の参照実装（LiveTickPlayer）と、
 *  MP の URL 組み立て・設定写像（buildMarketProfileUrl / MpFetchParams）。 */
const LIVE_PUBLIC_API_PATH = '/live/js/public/live_public_api.js';

/** ローソクの供給元（live core の /candles・T-10: live と同一データセット）。dashboard core は
 *  配信面を複製しない（ISSUE-348 と同型の取り違えを作らない）。period_presets と同じ
 *  「live から借りる」規約であり、単体起動（live 不在）では各タイルへ理由が掲示される。 */
const CANDLES_API_PREFIX = '/live';

/** チャート一覧のローソク本数（末尾から）。水準の照合ではなく文脈の表示が目的なので、
 *  タイル幅で読める程度に留める（増やすほど live core の I/O を 8 面ぶん引く）。 */
const CANDLE_LIMIT = 180;

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
 *                                       （既定は live 公開面の動的 import。検定は fake を注入）
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
  loadPeriodPresets = () => import(LIVE_PUBLIC_API_PATH),
  loadLiveTickPlayer = () => import(LIVE_PUBLIC_API_PATH),
  loadLiveMpApi = () => import(LIVE_PUBLIC_API_PATH),
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
      datasetRef: DEFAULT_DATASET_REF, timeframe: String(timeframe),
      maxBars: Number.MAX_SAFE_INTEGER,
    }).find((preset) => preset.bars === bars);
    return hit ? hit.label : null;
  };

  // 借用した MP プロファイル（依頼者承認 2026-09-06「MP 列＝ライブ MP の借用」）。
  //   null＝未着（まだ借りていない・借りられなかった）。版面は空欄になる。
  let mpProfile = null;
  /** 最新の 1m 形成中バー（MpFetchParams の `_getCandles` へ供給する）。 */
  let latestChartCandle = null;

  const ladderView = createReachSheetView({
    doc,
    periodAnnotator,
    now: () => Math.floor(clock() / 1000),
    // bin の決め方は domain（mp_bin.js）が唯一源。View は受けた密度を描くだけで、
    //   どの bin かも、どこから来たかも知らない（periodAnnotator と同型・裁定 6）。
    mpNormOf: (price) => mpNormAt(mpProfile, price),
  });
  const oscillatorView = createOscillatorSheetView({ doc, now: () => Math.floor(clock() / 1000) });
  // lwc は注入が無ければ global から解決する（unified_root は live vendor を読み込んでから
  //   dashboard を import する＝統合ページでは必ず居る。無い環境は View が文字で掲示する）。
  const chartLib = lwc !== undefined
    ? lwc
    : (typeof globalThis !== 'undefined' ? globalThis.LightweightCharts ?? null : null);
  const chartsView = createTimeframeChartsView({ doc, lwc: chartLib });

  /**
   * 表示系統の台帳（OCP）。
   *
   * 版面を 1 つ足すのは**ここへ 1 行**であり、mount / render / unmount の 3 箇所を
   * 手で足して回る必要はない（足し忘れは「モードを出ても残る版面」「更新されない版面」
   * として現れ、どちらも出力の検査では落ちにくい）。DOM の並びはこの順（左列: ラダー →
   * オシレーターラダー、右列: チャート）。置き場所そのものは CSS（dashboard.css の
   * grid-template-areas）が唯一源。
   *
   * `keyed` は「内容が直前と同じなら作り直さない」側か（省リソース段階 1）。差分適用で
   * 毎回受け取る版面（チャート）は false——同一内容では発行 0 が別途固定されている
   * （charts_paint_complexity）。
   */
  const panels = Object.freeze([
    { view: ladderView, keyed: true },
    { view: oscillatorView, keyed: true },
    { view: chartsView, keyed: false },
  ]);

  const client = transport
    ? createReachSheetClient({ fetch: transport, apiPrefix: prefix })
    : null;
  // 描けない環境（lwc 不在＝View が理由を掲示する）ではローソクを**取得しない**。取得だけ
  //   して捨てるのは「作ってから捨てる」型の浪費で、出力検証では落ちない（絶対命令 §4.1）。
  const candlesClient = transport && chartsLibUsable(chartLib)
    ? createCandlesClient({ fetch: transport, apiPrefix: candlesApiPrefix })
    : null;

  /** 何をいつ描き直すか（描画鍵・重複抑止・unchanged・状態トークン）の持ち主。 */
  const presenter = createSheetPresenter({
    dayStampOf: () => Math.floor(clock() / 86_400_000),
  });
  /** なめらか再生の申告と流し先の台帳。 */
  const tailLedger = createTailSpecLedger();

  let enabled = false;
  let poller = null;
  let candlePoller = null;
  let stopTimer = null;

  /**
   * 応答を各版面へ配る（描画は閉形式・ここで計算を発行しない）。
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
    const plan = presenter.accept(response);
    if (plan.full) {
      // 表の構成が変わりうるのは完全応答のときだけ。
      tailLedger.rebuild(plan.full);
    }
    for (const panel of panels) {
      if (panel.keyed) {
        if (plan.hasKeyed) panel.view.render(plan.keyed);
      } else if (plan.hasAlways) {
        panel.view.render(plan.always);
      }
    }
  }

  /**
   * 第 1 表だけをその場で描き直す（借用 MP の反映）。
   *
   * 借りたら**必ず 1 回描く**。応答（`/reach_sheet`）が unchanged だと present は版面を
   * 触らないため、ここで描き直さないと借用が版面に出ないまま捨てられる。逆に、版面が
   * 失敗を掲示している間は描き戻さない（判断は presenter が持つ・repaintTarget 参照）。
   */
  function repaintLadderInPlace() {
    const target = enabled ? presenter.repaintTarget() : null;
    if (!target) {
      return;
    }
    ladderView.render(target);
    // 直後の同一応答で二重に描き直さないよう、鍵を今描いた内容へ揃える。
    presenter.syncKey(target);
  }

  /** MP 借用の系統（公開面の検査・失敗文言・発行判定はすべて mp_borrow が持つ）。 */
  const mpBorrow = createMpBorrow({
    transport,
    apiPrefix: candlesApiPrefix,
    datasetRef: DEFAULT_DATASET_REF,
    timeframe: CHART_TIMEFRAME,
    barMs: TIMEFRAME_REFRESH_MS[CHART_TIMEFRAME],
    now: clock,
    loadLiveMpApi,
    getLatestCandle: () => latestChartCandle,
    readBundle: () => readInstanceBundle({ storage: templates }),
    isActive: () => enabled,
    onBorrowed: ({ note, profile, changed }) => {
      // 文言は**そのまま**流す（組み立てるのは失敗を観測した層・View は文言を作らない）。
      ladderView.setMpNote(note);
      if (changed) {
        mpProfile = profile;
        presenter.bumpGeneration();
      }
      repaintLadderInPlace();
    },
  });

  /** なめらか tick 再生の台（実装は live の LiveTickPlayer そのもの）。 */
  const tickPlayers = createLiveTickPlayers({
    transport,
    apiPrefix: candlesApiPrefix,
    datasetRef: DEFAULT_DATASET_REF,
    timeframes: DASHBOARD_TIMEFRAMES,
    chartTimeframe: CHART_TIMEFRAME,
    tailsLimit: OSC_TAILS_LIMIT,
    loadLiveTickPlayer,
    getComputeSpecs: () => tailLedger.specs(),
    onPrimaryBar: (bar) => {
      if (!enabled) {
        return;
      }
      ladderView.updateCurrentPrice(bar.close);
      // チャート足のタイルも同じ形成中バーで描く（依頼者指示 2026-08-31。
      //   同じ足の再生を 2 台立てると同一ストリームの二重取得になる）。
      chartsView.updateLastCandle(CHART_TIMEFRAME, bar);
      // MP の取得文脈（period='day' の窓下限・dispbp→barw）が読む最新足。
      //   既にここへ流れているものを分岐させるだけ＝取得は増えない。
      latestChartCandle = bar;
    },
    onBar: (timeframe, bar) => {
      if (enabled) {
        chartsView.updateLastCandle(timeframe, bar);
      }
    },
    onTails: (tails) => {
      if (!enabled) {
        return;
      }
      for (const [instanceId, seriesMap] of Object.entries(tails)) {
        const target = tailLedger.targetOf(instanceId);
        if (!target || !seriesMap) {
          continue;
        }
        const value = seriesMap[target.valueSeries];
        if (value === undefined) {
          continue;
        }
        oscillatorView.updateCellValue(target.indicatorId, target.timeframe, value);
      }
      // 第 1 表の水準価格（距離・価格・差）へも同じ tick の tails を流す。引き当ての
      //   合成は tailInstanceIdOf に閉じる（申告とずれない）。
      ladderView.updateLevelValues((instanceKey, series) => {
        const seriesMap = tails[tailInstanceIdOf(instanceKey)];
        return seriesMap ? seriesMap[series] : undefined;
      });
    },
  });

  /** ローソク 1 時間足ぶんの取得と供給（発行するかは candle_poller が決める）。 */
  async function issueCandles(timeframe) {
    const result = await candlesClient.fetchCandles({
      datasetRef: DEFAULT_DATASET_REF, timeframe, limit: CANDLE_LIMIT,
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

  /** 契機を 1 つ通す（発行するかは sheet_poller / candle_poller / mp_poller が決める）。 */
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
    mpBorrow.tick(bundle);
    return poller.tick({
      body: {
        dataset_ref: DEFAULT_DATASET_REF,
        chart_timeframe: CHART_TIMEFRAME,
        instances: bundle.instances,
        // 省リソース段階 2: 既知トークン。素材が不変ならサーバは unchanged を返す。
        //   bodyKey（同一周期の畳み込み）はこの欄を見ない＝畳み込みは従来どおり。
        known_state: presenter.stateToken(),
      },
      barCloseTime: barClock(),
    });
  }

  /** dashboard モードへ入るときに呼ばれる。器と版面を出し、発行を始める。 */
  async function enable() {
    if (enabled) {
      return;
    }
    const anchor = sheetHost.mount(host);   // アンカーが無ければここで落ちる（フェイルクローズ）。
    if (!anchor) {
      return;                                // DOM 非対応環境（描画対象そのものが無い）。
    }
    for (const panel of panels) {
      panel.view.mount(anchor);
    }
    enabled = true;
    // 器を出し直した直後は必ず完全応答が要る（unchanged では空の版面が残る）。
    presenter.reset();
    tailLedger.clear();

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
    // MP 借用はシートより**先に**始める（借用の着弾が失敗掲示を上書きしない規律は
    //   presenter.repaintTarget が持つ）。
    mpBorrow.start();

    await refresh();
    stopTimer = startTimer(() => { refresh(); }, TICK_INTERVAL_MS);

    tickPlayers.start();
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
    mpBorrow.stop();
    mpProfile = null;
    latestChartCandle = null;
    if (typeof stopTimer === 'function') {
      stopTimer();
      stopTimer = null;
    }
    tickPlayers.stop();
    for (const panel of panels) {
      panel.view.unmount();
    }
    sheetHost.unmount();
  }

  return { enable, disable, refresh };
}
