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
import { createMpProfileClient } from './mp_profile_client.js';
import { createMpFetchContext } from './mp_fetch_context.js';
import { createLiveTicksFeed } from './live_ticks_client.js';
import { readInstanceBundle, DASHBOARD_TIMEFRAMES } from './template_binding_reader.js';
import { TIMEFRAME_REFRESH_MS } from './timeframes.js';
import { createSheetPoller } from '../../usecase/sheet_poller.js';
import { createCandlePoller } from '../../usecase/candle_poller.js';
import { createMpPoller } from '../../usecase/mp_poller.js';
import { mpNormAt } from '../../domain/mp_bin.js';

/** 素材（arch-spec T-10: live と同一データセット固定）。 */
const DATASET_REF = 'jp225_tick';

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
 *  ＝本数のみ表示に縮退）と、なめらか tick 再生の参照実装（LiveTickPlayer）。 */
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
      datasetRef: DATASET_REF, timeframe: String(timeframe),
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
  /** 借用 MP の発行判定（live 公開面が届くまで null＝1 本も発行しない）。 */
  let mpPoller = null;
  /** 借用クライアント / 取得文脈（同上）。 */
  let mpClient = null;
  let mpFetchContext = null;
  /** 借用プロファイルの世代（描画鍵に混ぜ、借用が版面へ確実に反映されるようにする）。 */
  let mpGeneration = 0;
  /** なめらか tick 再生（live の LiveTickPlayer を借りる）。時間足ごとに 1 台
   *  （主＝チャート足がラダー・第 2 表・1m タイルを、他の 7 台が各タイルを駆動する・
   *  依頼者指示 2026-08-31「各時間足のチャートもライブモードと同じティック粒度」）。
   *  空＝未稼働（縮退表示）。 */
  let tickPlayers = [];
  /** enable 中フラグ（player の非同期 import が disable 後に着弾したら捨てるための札）。 */
  let tickPlayerWanted = false;
  /** サーバの状態トークン（省リソース段階 2・依頼者承認 2026-08-30）。次要求の known_state に
   *  載せ、素材が不変ならサーバは unchanged の極小応答を返す（シート計算ごと省かれる）。 */
  let sheetState = null;
  /** 直近の完全応答（unchanged 時のチャート差分再描画＝アンカー再試行の材料）。 */
  let lastFullResponse = null;
  /** 直近に描いた内容の鍵（省リソース段階 1: 同一内容なら第 1・第 2 表を作り直さない）。 */
  let lastRenderedKey = null;
  /** 直近に**描いた**応答が成功だったか（借用の着弾が失敗掲示を上書きしないための札）。 */
  let lastPresentedOk = false;
  /** なめらか再生の spec 台帳（唯一源＝完全応答・依頼者指示 2026-08-31）。第 2 表のセルと
   *  第 1 表の行になった instance **だけ**を申告する（表に出ない instance へ tails を
   *  計算させると使わない計算を発行することになる・絶対命令 §4.1）。 */
  let tailSpecs = [];
  /** instanceId（instance_key の合成）→ 第 2 表の流し先（行列と系列名）。 */
  const oscTailTargets = new Map();

  /** instance_key（4 要素）→ tails の申告 ID。合成の定義は**ここ 1 箇所**（specs の申告と
   *  tails の引き当てが同じ関数を通る＝ずれない）。 */
  function tailInstanceIdOf(key) {
    return key.join('\u0000');
  }

  /** instance_key から /live_ticks の spec を 1 本組む（不能なら null）。 */
  function tailSpecOf(key) {
    if (!Array.isArray(key) || key.length !== 4) {
      return null;
    }
    let params;
    try {
      params = JSON.parse(key[2]);   // params_key は json.dumps＝JSON として復元できる（契約）。
    } catch {
      return null;
    }
    if (!params || typeof params !== 'object') {
      return null;
    }
    return {
      instanceId: tailInstanceIdOf(key),
      indicatorId: key[0],
      variant: key[1],
      // 計算足は instance の軸（ISSUE-274 の MTF override と同じ規約で params.timeframe に載せる）。
      params: { ...params, timeframe: key[3] },
    };
  }

  /** 完全応答（cells＋rows）から tails の申告と流し先を組み直す。 */
  function rebuildTailSpecs(response) {
    tailSpecs = [];
    oscTailTargets.clear();
    const declared = new Set();
    const declare = (key) => {
      const spec = tailSpecOf(key);
      if (!spec || declared.has(spec.instanceId)) {
        return spec ? spec.instanceId : null;
      }
      declared.add(spec.instanceId);
      tailSpecs.push(spec);
      return spec.instanceId;
    };
    for (const cell of (Array.isArray(response.cells) ? response.cells : [])) {
      if (!cell || !cell.value_series) {
        continue;   // 宣言の無いセル（旧応答・積算セル等）は流さない＝従来の 1s 表示のまま。
      }
      const instanceId = declare(cell.instance_key);
      if (instanceId !== null && !oscTailTargets.has(instanceId)) {
        oscTailTargets.set(instanceId, {
          indicatorId: cell.indicator_id,
          timeframe: cell.timeframe,
          valueSeries: cell.value_series,
        });
      }
    }
    // 第 1 表（依頼者指示 2026-08-31: 距離・価格・差もライブチャート粒度）。行の instance を
    //   申告する。流し先の解決は View 側（instance_key × series → 行）なのでここでは申告のみ。
    for (const row of (Array.isArray(response.rows) ? response.rows : [])) {
      if (row && typeof row.series === 'string' && row.series) {
        declare(row.instance_key);
      }
    }
  }

  /**
   * 描画済みの内容を表す鍵（省リソース段階 1）。
   *
   * 日付印を含める＝到達時刻の「今日/昨日」表記が日替わりで確実に描き直される。
   * 借用 MP の世代も含める——プロファイルが入れ替わったのに応答が同一だと、鍵が変わらず
   * MP 列だけが古いまま残る（借りたのに描かない＝作って捨てる計算になる）。
   */
  function renderKeyOf(response) {
    return `${Math.floor(clock() / 86_400_000)}|${mpGeneration}|${JSON.stringify(response)}`;
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
      // なめらか再生の申告: 表の構成が変わりうるのは完全応答のときだけ。
      rebuildTailSpecs(response);
    }
    // 版面がいま「成功した応答」を映しているか。借用の着弾が失敗掲示を上書きしないための札
    //   （下の applyMpProfile を参照）。unchanged はここへ来ないので、直近に**描いた**内容を表す。
    lastPresentedOk = !!(response && response.ok === true);
    // 省リソース段階 1: 内容が直前の描画と同一なら第 1・第 2 表を作り直さない
    //   （毎秒の全再構築は内容不変時にはまるごと浪費・依頼者指摘 2026-08-30）。
    const key = renderKeyOf(response);
    if (key !== lastRenderedKey) {
      ladderView.render(response);
      oscillatorView.render(response);
      lastRenderedKey = key;
    }
    // チャート一覧は**同じ応答**で描く（ISSUE-452 禁止事項: 二重発行の不在）。差分適用のみ
    //   なので同一内容では発行 0（charts_paint_complexity で固定済み）。
    chartsView.render(response);
  }

  /**
   * 借りたプロファイルを版面へ適用する（発行 − 使用 = 0 の「使用」側）。
   *
   * 借りたら**必ず 1 回描く**。応答（`/reach_sheet`）が unchanged だと present は版面を
   * 触らないため、ここで描き直さないと借用が版面に出ないまま捨てられる。逆に、借りて
   * いないのに描き直すこともしない（描き直しは鍵の世代でだけ起こる）。
   *
   * ただし**版面が失敗を掲示している間は描き戻さない**。`lastFullResponse` は直近の
   * *成功* 応答なので、無条件に描くとシートが落ちている最中に古い行が復活し、理由の掲示も
   * 消える——ユーザーには復旧したように見える（最も危険な縮退）。合成根は MP をシートより
   * 先に発行するため、この順序（失敗掲示 → 借用の着弾）は実際に起こる。
   * このとき profile は保持だけしておき、次の成功応答が `mpGeneration` 経由で拾う。
   */
  function applyMpProfile(profile) {
    mpProfile = profile;
    mpGeneration += 1;
    if (!enabled || !lastFullResponse || !lastPresentedOk) {
      return;
    }
    ladderView.render(lastFullResponse);
    // 直後の同一応答で二重に描き直さないよう、鍵を今描いた内容へ揃える。
    lastRenderedKey = renderKeyOf(lastFullResponse);
  }

  /** MP を 1 本借りて版面へ流す（発行するかは mp_poller が決める）。 */
  async function issueMpProfile() {
    const result = await mpClient.fetchProfile(mpFetchContext.context());
    if (!enabled) {
      return result;   // モードを出た後の遅延着弾は捨てる（present と同じ 1 箇所ガード）。
    }
    if (result.ok) {
      ladderView.setMpNote(null);
      applyMpProfile(result.profile);
    } else {
      // 無言縮退の禁止: 列が空のとき「密度が無い相場」と区別が付く形で理由を掲示する。
      //   文言は**そのまま**流す（組み立てるのは失敗を観測した mp_profile_client だけ・
      //   candles_client → chartsView.setCandleError と同じ受け渡し）。ここで頭に文を足すと
      //   版面に同じ主語が二重に出る。
      ladderView.setMpNote(result.error.message);
      applyMpProfile(null);
    }
    return result;
  }

  /**
   * MP 借用の契機を 1 つ通す（依頼者裁定 2026-09-06 案 a: 契機はライブと完全同期）。
   *
   * 契機は 3 つ——1m バー枠の進み・有効化直後の初回・テンプレートの MP 設定の変化。
   * どれを発行に変えるかは mp_poller が決める（ここは契機を渡すだけ・View は発行しない）。
   *
   * @param {?object} [bundle] 既に読んだ instance 束（無ければここで読む）
   */
  function tickMpBorrow(bundle = null) {
    if (!enabled || !mpPoller || !mpFetchContext) {
      return;
    }
    const read = bundle ?? readInstanceBundle({ storage: templates });
    if (!read.ok) {
      return;   // 束が組めない理由は present が既に掲示している（二重に出さない）。
    }
    // どの instance の設定を借りるか（裁定 7）は mp_fetch_context が持つ——束の記録の形を
    //   知るのは設定を写す役であって、結線ではない。
    mpFetchContext.setFromBundle(read);
    mpPoller.tick({ paramsKey: mpFetchContext.settingsKey() });
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
    tickMpBorrow(bundle);
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
    tailSpecs = [];
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
    // MP 列＝ライブ MP の借用（依頼者承認 2026-09-06・第 1 段階）。URL の組み立てと設定の
    //   写像はライブの唯一源（buildMarketProfileUrl / MpFetchParams）を公開面から借りる
    //   ——写すとライブが 1 パラメータ足した瞬間にラダーだけ古い URL を投げ、メモの共有も
    //   仕様の同期も無言で壊れる。公開面が読めない環境（単体起動・live 停止）では MP 列は
    //   空欄のまま＝借用しないので発行も 0（取得だけして捨てる経路を作らない）。
    if (transport) {
      loadLiveMpApi().then((mod) => {
        if (!enabled || mpPoller) {
          return;   // モードを出た後の着弾・二重結線は静かに捨てる（異常ではない）。
        }
        if (!mod || typeof mod.buildMarketProfileUrl !== 'function'
            || typeof mod.MpFetchParams !== 'function') {
          // 面はあるが名前が無い＝公開面の再輸出が消えた / live 側の配置換え。
          //   黙って返すと MP 列がただ空になり、借用が始まってすらいないことが
          //   版面からも読めない（無言縮退の禁止）。
          throw new TypeError('ライブの公開面に MP の借用口がありません');
        }
        mpClient = createMpProfileClient({
          fetch: transport, apiPrefix: candlesApiPrefix, buildUrl: mod.buildMarketProfileUrl,
        });
        mpFetchContext = createMpFetchContext({
          MpFetchParams: mod.MpFetchParams,
          datasetRef: DATASET_REF,
          timeframe: CHART_TIMEFRAME,
          // ライブの src 既定（テンプレートに MP が無いときだけ使う）。'zp' のリテラルを
          //   dashboard 側に持たない——ライブが既定を変えたら黙ってずれる。
          defaultSource: mod.MP_DEFAULT_SOURCE,
          // 最新 1m 足は既存の LiveTickPlayer 供給を流用する（第 2 の取得口を作らない）。
          getLatestCandle: () => latestChartCandle,
          nowSec: () => Math.floor(clock() / 1000),
        });
        mpPoller = createMpPoller({
          issue: issueMpProfile,
          now: clock,
          // 枠の判定はチャート足のバー周期（candle_poller と同じ表・同じ式）。
          barMs: TIMEFRAME_REFRESH_MS[CHART_TIMEFRAME],
        });
        tickMpBorrow();   // 有効化直後の初回（3 契機のうちの 1 つ）。
      }).catch((err) => {
        // 公開面を読めない（live 停止・単体起動・配置換え・再輸出の削除）。借用は始まらない
        //   ので発行は 0 のままだが、**なぜ MP 列が空なのか**は版面に出す。文言の書き手は
        //   ここ 1 か所（View は文言を組み立てない・setMpNote の規約）。
        if (!enabled) {
          return;
        }
        ladderView.setMpNote(`MP を借用できません: ${err && err.message ? err.message : err}`);
        if (lastFullResponse && lastPresentedOk) {
          ladderView.render(lastFullResponse);
          lastRenderedKey = renderKeyOf(lastFullResponse);
        }
      });
    }

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
        if (!tickPlayerWanted || tickPlayers.length > 0
            || !mod || typeof mod.LiveTickPlayer !== 'function') {
          return;
        }
        const feed = createLiveTicksFeed({ fetch: transport, apiPrefix: candlesApiPrefix });
        // タイマは**必ずラップして**渡す（下の主 player のコメント参照）。
        const timers = {
          setInterval: (...args) => globalThis.setInterval(...args),
          clearInterval: (...args) => globalThis.clearInterval(...args),
        };
        tickPlayers.push(new mod.LiveTickPlayer({
          renderer: {
            updateLastCandle: (bar) => {
              if (enabled && bar) {
                ladderView.updateCurrentPrice(bar.close);
                // チャート足のタイルも同じ形成中バーで描く（依頼者指示 2026-08-31。
                //   同じ足の再生を 2 台立てると同一ストリームの二重取得になる）。
                chartsView.updateLastCandle(CHART_TIMEFRAME, bar);
                // MP の取得文脈（period='day' の窓下限・dispbp→barw）が読む最新足。
                //   既にここへ流れているものを分岐させるだけ＝取得は増えない。
                latestChartCandle = bar;
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
          getComputeSpecs: () => tailSpecs,
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
            // 第 1 表の水準価格（距離・価格・差）へも同じ tick の tails を流す。引き当ての
            //   合成は tailInstanceIdOf に閉じる（申告とずれない）。
            ladderView.updateLevelValues((instanceKey, series) => {
              const seriesMap = tails[tailInstanceIdOf(instanceKey)];
              return seriesMap ? seriesMap[series] : undefined;
            });
          },
          // タイマのラップ必須の理由: player は `this._setInterval(...)` とメソッド形で呼ぶため、
          //   素の globalThis.setInterval を渡すと this が Window でなくなり "Illegal invocation"
          //   で start が黙って死ぬ（実測 2026-08-31。live 側は bootstrap がバインド済みを注入）。
          ...timers,
        }));
        // 残りの 7 足へタイル駆動の player を 1 台ずつ（依頼者指示 2026-08-31）。ライブモードの
        //   その足のチャートと**同一の経路**（/live_ticks の barTimes・/forming_bar シード・
        //   12 秒遅延・100ms 適用）。tails の申告は主 player だけ（同じ末尾値を 8 回計算させる
        //   のは使わない計算の発行・絶対命令 §4.1）。
        for (const timeframe of DASHBOARD_TIMEFRAMES) {
          if (timeframe === CHART_TIMEFRAME) {
            continue;
          }
          tickPlayers.push(new mod.LiveTickPlayer({
            renderer: {
              updateLastCandle: (bar) => {
                if (enabled && bar) {
                  chartsView.updateLastCandle(timeframe, bar);
                }
              },
            },
            fetchLiveTicks: feed.fetchLiveTicks,
            loadFormingBar: feed.loadFormingBar,
            datasetRef: DATASET_REF,
            getTimeframe: () => timeframe,
            ...timers,
          }));
        }
        tickPlayers.forEach((player) => player.start());
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
    if (mpPoller) {
      mpPoller.stop();
      mpPoller = null;
    }
    mpClient = null;
    mpFetchContext = null;
    mpProfile = null;
    latestChartCandle = null;
    if (typeof stopTimer === 'function') {
      stopTimer();
      stopTimer = null;
    }
    tickPlayerWanted = false;
    tickPlayers.forEach((player) => player.stop());
    tickPlayers = [];
    ladderView.unmount();
    oscillatorView.unmount();
    chartsView.unmount();
    sheetHost.unmount();
  }

  return { enable, disable, refresh };
}
