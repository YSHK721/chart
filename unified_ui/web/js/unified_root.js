// 統合エントリのブートストラップ（Green — 本体実装）。基本設計書 §3 / §4。
//
// 役割:
//   - live / replay の composition root を **動的 import** で配線する（起動時はアクティブモード
//     のみ・反対モードはトグル時に読み込む＝障害隔離）。
//   - モードトグルで「反対 core 事前ロード（失敗＝現モード継続）→ 現状態 capture → SW へアクティブ
//     モード通知 → 現配線 teardown（timer clearAll + chart dispose + mode-ui pristine 復元）→
//     反対モード bootstrap（replay は setupReplay 併用）→ restore」を実行する。
//   - Service Worker（/sw.js）を登録し、既存フロントの root 相対 API fetch をモード別 prefix へ
//     リライトさせる。SW が制御に入れない環境は **フェイルクローズ**（明示エラー・mount しない）。
//
// 無波及順守（既存 indigators/** · simulator/** は 1 行も編集しない）:
//   - 既存 bootstrap は setInterval/clearInterval/storage の注入口を持つ（実証済み）。
//     timer_registry.wrap でラップしたタイマを注入し、切替時に clearAll で一括停止する。
//   - localStorage 名前空間分離は storage ポート（getItem/setItem/removeItem）を mode_storage の
//     scopedStorage でラップして注入する（既存 LocalStorageGateway は無改変）。
//   - bind() は永続ツールバー/ダイアログへ removeEventListener 無しで addEventListener する
//     （indicator_controller.js:900-951 で実証）。teardown で mode-ui サブツリーを pristine
//     innerHTML へ戻すことで、**要素スコープ**のリスナは新ノード置換で根絶する（🟡-6）。
//     ただし既存無編集モジュールが document/body スコープへ張るリスナ（例 timeframe_menu.js:95 の
//     `doc.addEventListener('click', …)`）は innerHTML 復元では消えず、**トグル毎に +1 残存**する
//     ＝無波及制約下の既知限界（機能影響は軽微・有界／ISSUE-169）。完全根絶は既存改変を要するため
//     将来別承認課題。

import { wrap as wrapTimers } from './timer_registry.js';
import { captureState, restoreState } from './view_state.js';
import { scopedStorage } from './mode_storage.js';

const DATASET_REF = 'jp225_tick';
const MODE = Object.freeze({ LIVE: 'live', REPLAY: 'replay' });

// 各モードの core モジュール URL（動的 import 対象＝反対 core への起動時依存を持たない）。
const CORE_SPEC = {
  [MODE.LIVE]: { root: '/live/js/adapter/front/composition_root_front.js' },
  [MODE.REPLAY]: {
    root: '/replay/js/adapter/front/composition_root_front.js',
    replay: '/replay/js/replay.js',
  },
};

let current = null; // { mode, boot, registry }
let activeMode = MODE.LIVE;
let switching = false;
let pristineModeUi = null; // #mode-ui の初期 innerHTML（teardown 復元源）。
let lwcLoaded = false;

// ---- capture/restore 用の view アダプタ（controller/chart 実体を抽象化）--------
// timeframe・indicators はモード別 scoped localStorage が復元源（controller.restore()）。
// ここでは可視レンジ（scroll 位置＝非永続）の carry-over を担う。既存 setter 署名は推測しない。
function viewAdapter(boot) {
  const chart = boot && boot.chart;
  const controller = boot && boot.controller;
  const timeScale = () => {
    try {
      return chart && typeof chart.timeScale === 'function' ? chart.timeScale() : null;
    } catch {
      return null;
    }
  };
  return {
    getTimeframe: () => (controller ? controller._timeframe : null),
    getIndicators: () => null, // モード別 scoped 永続が復元源（cross-mode 強制適用はしない）。
    getVisibleRange: () => {
      const ts = timeScale();
      try {
        const r = ts && typeof ts.getVisibleRange === 'function' ? ts.getVisibleRange() : null;
        return r && r.from != null && r.to != null
          ? { from: Number(r.from), to: Number(r.to) }
          : null;
      } catch {
        return null;
      }
    },
    setTimeframe: () => {
      /* controller.restore() がモード別 scoped localStorage から復元（署名推測回避）。 */
    },
    setIndicators: () => {
      /* 同上（指標構成はモード別永続が復元源）。 */
    },
    setVisibleRange: (range) => {
      if (!range) {
        return;
      }
      const ts = timeScale();
      try {
        if (ts && typeof ts.setVisibleRange === 'function') {
          ts.setVisibleRange(range);
        }
      } catch {
        /* 可視レンジ復元は best-effort（データ差でレンジ外なら無視）。 */
      }
    },
  };
}

// ---- 反対モードの bootstrap 用の共通注入を組む --------------------------------
function buildInjection(mode) {
  const registry = wrapTimers({
    setInterval: globalThis.setInterval.bind(globalThis),
    clearInterval: globalThis.clearInterval.bind(globalThis),
  });
  const injection = {
    lwc: window.LightweightCharts,
    container: document.getElementById('chart'),
    doc: document,
    storage: scopedStorage(globalThis.localStorage, mode),
    datasetRef: DATASET_REF,
    // 既存 bootstrap の setInterval/clearInterval 注入口へラップ済みタイマを渡す。
    setInterval: registry.setInterval,
    clearInterval: registry.clearInterval,
  };
  return { registry, injection };
}

// ---- core モジュールの動的ロード（🔴-1: 反対 core は toggle 時のみ・失敗は呼び出し側で捕捉）--
async function loadCore(mode) {
  const spec = CORE_SPEC[mode];
  const rootMod = await import(spec.root);
  if (mode === MODE.REPLAY) {
    const replayMod = await import(spec.replay);
    return {
      bootstrap: rootMod.bootstrap,
      recentBars: rootMod.RECENT_BARS,
      setupReplay: replayMod.setupReplay,
    };
  }
  return { bootstrap: rootMod.bootstrap };
}

// ---- lightweight-charts（vendor）を **アクティブモードの prefix** から動的ロード（🔴-1 隔離）--
function loadVendor(mode) {
  if (lwcLoaded || window.LightweightCharts) {
    lwcLoaded = true;
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    const s = document.createElement('script');
    s.src = `/${mode}/vendor/lightweight-charts.js`;
    s.onload = () => {
      lwcLoaded = !!window.LightweightCharts;
      resolve(lwcLoaded);
    };
    s.onerror = () => resolve(false);
    document.head.appendChild(s);
  });
}

// ---- 各モードのマウント（preloaded module を受けて起動シーケンスを実行）----------
async function mountLive(mod) {
  const { registry, injection } = buildInjection(MODE.LIVE);
  const boot = await mod.bootstrap(injection);
  boot.controller.bind();
  await boot.ready;
  boot.controller.restore();
  if (boot.liveUpdater) {
    boot.liveUpdater.start();
  }
  if (boot.formingBarUpdater) {
    boot.formingBarUpdater.start();
  }
  if (boot.liveTickPlayer) {
    boot.liveTickPlayer.start();
  }
  // 売買マーカーの初回 load（entry が制御する URL＝モード prefix を明示して router へ回す）。
  if (boot.tradeMarkers) {
    boot.tradeMarkers.load('/live/data/trade_markers.json');
  }
  return { mode: MODE.LIVE, boot, registry };
}

async function mountReplay(mod) {
  const { registry, injection } = buildInjection(MODE.REPLAY);
  const boot = await mod.bootstrap(injection);
  boot.controller.bind();
  await boot.ready;
  boot.controller.restore();
  await mod.setupReplay({
    chart: boot.chart,
    mainSeries: boot.mainSeries,
    controller: boot.controller,
    renderer: boot.renderer,
    datasetRef: DATASET_REF,
    recentBars: mod.recentBars,
    document,
    marketProfile: boot.marketProfile,
  });
  return { mode: MODE.REPLAY, boot, registry };
}

function mount(mode, mod) {
  return mode === MODE.REPLAY ? mountReplay(mod) : mountLive(mod);
}

// ---- teardown（旧配線の停止・破棄・DOM 冪等化）--------------------------------
function teardown(state) {
  if (state) {
    // 1. 旧モードのタイマを一括停止（ライブ更新・forming・tick 再生・replay ループ）。
    try {
      state.registry.clearAll();
    } catch {
      /* no-op */
    }
    // 2. チャート実体を dispose（lightweight-charts の canvas/購読を解放）。
    try {
      const chart = state.boot && state.boot.chart;
      if (chart && typeof chart.remove === 'function') {
        chart.remove();
      }
    } catch {
      /* no-op */
    }
  }
  // 3. mode-ui サブツリーを pristine へ復元。innerHTML 置換で全ノードが無リスナの新規要素へ入れ替わり、
  //    **要素スコープ**の bind() リスナは多重化しない。ただし既存無編集モジュールが document/body スコープ
  //    へ張るリスナ（timeframe_menu.js:95 の doc click 等）は消えず、トグル毎に +1 残存する（既知限界・
  //    軽微有界・ISSUE-169）。完全根絶は既存改変が必要＝無波及制約下では不可（将来別承認課題）。
  const modeUi = document.getElementById('mode-ui');
  if (modeUi && pristineModeUi != null) {
    modeUi.innerHTML = pristineModeUi;
  }
}

// ---- Service Worker: アクティブモード通知（ack 待ち）--------------------------
function notifySwMode(mode) {
  return new Promise((resolve) => {
    const ctrl =
      typeof navigator !== 'undefined' && navigator.serviceWorker
        ? navigator.serviceWorker.controller
        : null;
    if (!ctrl) {
      resolve(false);
      return;
    }
    const channel = new MessageChannel();
    let settled = false;
    const done = (ok) => {
      if (!settled) {
        settled = true;
        resolve(ok);
      }
    };
    channel.port1.onmessage = (event) => {
      done(!!(event.data && event.data.ok));
    };
    try {
      ctrl.postMessage({ type: 'set-mode', mode }, [channel.port2]);
    } catch {
      done(false);
      return;
    }
    // ack が来ない環境でも進行を止めない（保険）。
    setTimeout(() => done(false), 500);
  });
}

// ---- エラー表示（フェイルクローズ / モード読込失敗）--------------------------
function showModeError(message) {
  const el = document.getElementById('mode-error');
  if (el) {
    el.textContent = message;
    el.style.display = 'block';
  }
  // eslint-disable-next-line no-console
  console.error('[unified_root]', message);
}

function clearModeError() {
  const el = document.getElementById('mode-error');
  if (el) {
    el.style.display = 'none';
    el.textContent = '';
  }
}

// ---- モード UI 反映（css 切替・body クラス・リプレイトグル点灯）------------------
//   body クラス um-mode-live / um-mode-replay が replay-bar / live-follow-toggle の表示を
//   CSS で制御する。「リプレイ」トグルは両モードで表示し、aria-pressed で on/off を反映する。
function applyModeUi(mode) {
  const css = document.getElementById('mode-css');
  if (css) {
    css.setAttribute('href', `/${mode}/css/app.css`);
  }
  document.body.classList.toggle('um-mode-live', mode === MODE.LIVE);
  document.body.classList.toggle('um-mode-replay', mode === MODE.REPLAY);
  // 「リプレイ」トグルの点灯状態（replay=on / live=off）。
  const replayToggle = document.getElementById('enter-replay');
  if (replayToggle) {
    replayToggle.setAttribute('aria-pressed', mode === MODE.REPLAY ? 'true' : 'false');
  }
}

// ---- トグル動作（🔴-1 隔離: 反対 core を先にロードし、失敗時は現モードを壊さない）--------
async function toggle(nextMode) {
  if (switching || nextMode === activeMode) {
    return;
  }
  switching = true;
  try {
    // 1. 反対モードの vendor（lightweight-charts）と core を **teardown 前に** 事前ロードする。
    //    landing core が停止していて vendor/core 未ロードでも、健全な反対モード core から取得できる
    //    （🟡-A: 隔離の非対称解消＝どちらの core がダウンでも健全側へ到達可能）。ロード済みなら
    //    loadVendor は即返し（lwcLoaded ガード）、import はブラウザキャッシュで再取得しない。
    //    いずれか失敗＝当該モード core 停止なら、現表示を一切壊さず離脱する。
    const vendorOk = await loadVendor(nextMode);
    if (!vendorOk) {
      showModeError(
        `${nextMode} モードを起動できません（${nextMode} core 停止中の可能性・vendor 未取得）。`
          + `現在の ${activeMode} 表示は継続します。`,
      );
      return;
    }
    let mod;
    try {
      mod = await loadCore(nextMode);
    } catch {
      showModeError(
        `${nextMode} モードを起動できません（${nextMode} core が停止中の可能性）。`
          + `現在の ${activeMode} モードは継続します。`,
      );
      return;
    }
    // 2. 現状態 capture（未マウント時は null）。
    const captured = current ? captureState(viewAdapter(current.boot)) : null;
    // 3. SW へアクティブモード通知（以降の API fetch が新 prefix で回る）。
    await notifySwMode(nextMode);
    // 4. 現配線 teardown（timer clearAll + chart dispose + mode-ui pristine 復元）。
    teardown(current);
    current = null;
    // 5. 反対モード bootstrap（＋replay は setupReplay）。
    activeMode = nextMode;
    applyModeUi(nextMode);
    try {
      current = await mount(nextMode, mod);
      // pristine 復元で作り直された「リプレイ」トグル（#enter-replay）を再配線する。
      wireModeSwitchButtons();
      clearModeError();
    } catch (err) {
      showModeError(`${nextMode} モードの初期化に失敗しました: ${err && err.message ? err.message : err}`);
      return;
    }
    // 6. restore（可視レンジ carry-over。timeframe/指標はモード別 scoped 永続が復元）。
    if (captured && current) {
      restoreState(viewAdapter(current.boot), captured);
    }
  } finally {
    switching = false;
  }
}

// ---- Service Worker 登録（フェイルクローズ判定つき）--------------------------
// 戻り値: SW がページを制御している（＝API リライトが効く）なら true。
async function registerServiceWorker() {
  if (typeof navigator === 'undefined' || !navigator.serviceWorker) {
    return false; // 非対応ブラウザ（module SW 非対応等）。呼び出し側でフェイルクローズ。
  }
  try {
    await navigator.serviceWorker.register('/sw.js', { type: 'module', scope: '/' });
    await navigator.serviceWorker.ready;
  } catch {
    return false;
  }
  if (navigator.serviceWorker.controller) {
    return true;
  }
  // まだ制御下でない（初回訪問）。一度だけリロードして制御下に入る。
  const RELOAD_GUARD = 'unified_sw_reloaded';
  if (!sessionStorage.getItem(RELOAD_GUARD)) {
    sessionStorage.setItem(RELOAD_GUARD, '1');
    location.reload();
    await new Promise(() => {}); // reload 後に再実行されるため待機。
  }
  // リロード後も制御下に入れない＝この環境では SW が機能しない。
  return !!navigator.serviceWorker.controller;
}

// ---- リプレイ トグルボタン配線（#mode-ui 内＝mount 毎に新ノードへ再配線）--------
//   「リプレイ」(#enter-replay) はオン・オフのトグル＝現モードに応じて反対モードへ切替える
//   （live→replay / replay→live）。ツールバー内＝teardown の pristine 復元で作り直されるため
//   mount 毎に再配線する（毎回新要素＝リスナ蓄積なし）。landing mount 前にも 1 回配線しておく
//   ことで、landing core ダウンで mount に失敗しても反対モードへ切替できる（🟡-A: 隔離の非対称解消）。
function wireModeSwitchButtons() {
  const btn = document.getElementById('enter-replay');
  if (btn) {
    btn.addEventListener('click', () => toggle(activeMode === MODE.REPLAY ? MODE.LIVE : MODE.REPLAY));
  }
}

// ---- 起動 --------------------------------------------------------------------
async function main() {
  // mode-ui の pristine スナップショット（bind() 前＝無リスナ状態）を保持する（🟡-6 復元源）。
  const modeUi = document.getElementById('mode-ui');
  pristineModeUi = modeUi ? modeUi.innerHTML : null;

  // Service Worker が制御下に入れないと root 相対 API fetch がリライトされず router が 404 する。
  //   → フェイルクローズ（🟡-3）: 明示エラーを出し mount しない（silent 404 を避ける）。
  const swControlled = await registerServiceWorker();
  if (!swControlled) {
    showModeError(
      'Service Worker を有効化できないため起動を中止しました。ページをリロードするか、'
        + 'ES Modules Service Worker 対応ブラウザで開いてください'
        + '（未対応だと API 要求がルーティングされず動作しません）。',
    );
    return;
  }

  // 切替ボタンは **landing mount 前** に配線する（🟡-A: landing core がダウンで初期 mount に失敗しても、
  //   ユーザーは健全な反対モードへ切替できる＝隔離の非対称を解消）。以降は toggle 毎に mount 後へ再配線。
  wireModeSwitchButtons();
  applyModeUi(activeMode);
  await notifySwMode(activeMode);

  // landing mount 区間を switching で相互排他保護する（landing-vs-toggle レースの確定的封鎖）。
  //   toggle() 冒頭の `if (switching || …) return` が landing 中のクリックを弾き、landing 完了まで
  //   トグルは no-op（トグル配線自体は上で完了済み＝存在はする）。finally で必ず false へ戻すため、
  //   landing が早期 return（core ダウン）する経路でも解除漏れは起きない＝二重 mount は構造的に不可能。
  switching = true;
  try {
    // landing（アクティブモード）の vendor（lightweight-charts）を当該 core からロードする（🔴-1 隔離）。
    //   失敗＝landing core 停止でも、トグルは既に配線済み＝反対モードへ切替可能（早期 return で可）。
    const ok = await loadVendor(activeMode);
    if (!ok) {
      showModeError(
        `${activeMode} モードを起動できません（${activeMode} core 停止中の可能性・vendor 未取得）。`
          + `ツールバーの「リプレイ」ボタンで反対モードへ切り替えてください。`,
      );
      return;
    }
    try {
      const mod = await loadCore(activeMode);
      current = await mount(activeMode, mod);
    } catch (err) {
      showModeError(
        `${activeMode} モードを起動できません（${activeMode} core 停止中の可能性・ツールバーの「リプレイ」ボタンで反対モードへ）: `
          + `${err && err.message ? err.message : err}`,
      );
    }
  } finally {
    switching = false;
  }
}

main().catch((err) => {
  showModeError(`初期化に失敗しました: ${err && err.message ? err.message : err}`);
});

// テスト・診断用途に公開（実 UI 動作には影響しない）。
export { toggle, MODE };
