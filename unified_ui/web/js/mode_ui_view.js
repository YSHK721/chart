// UI/DOM アダプタ（統合ルートから抽出・葉モジュール）。
//
// 役割:
//   - lightweight-charts（vendor）の動的ロード（loadVendor）。
//   - モード読込失敗・フェイルクローズのエラー表示（showModeError / clearModeError）。
//   - モード UI 反映（body クラス・リプレイトグル点灯）（applyModeUi）。
//   - リプレイトグルボタンの配線（wireModeSwitchButtons）。
//
// 無波及順守: 本モジュールは unified_root を import しない（葉モジュール＝循環依存なし）。
//   関数実体は unified_root.js から**ロジック無改変で移設**したもの（DOM id・SW 通知ワード・
//   vendor URL は不変）。MODE は applyModeUi が依存する定数のため同時に移設し、unified_root は
//   これを import して再 export する（公開 API 不変）。wireModeSwitchButtons は unified_root の
//   module 内状態 modeController を参照していたため、その値を引数注入に変更（modeController は
//   main() 内で 1 度だけ生成され本関数呼び出しの直前に確定・以降不変＝挙動等価）。

export const MODE = Object.freeze({ LIVE: 'live', REPLAY: 'replay' });

let lwcLoaded = false;

// ---- lightweight-charts（vendor）を live prefix から動的ロード（両 core とも同一 vendor を配信）----
export function loadVendor(mode) {
  if (lwcLoaded || (typeof window !== 'undefined' && window.LightweightCharts)) {
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

// ---- エラー表示（フェイルクローズ / モード読込失敗）--------------------------
export function showModeError(message) {
  if (typeof document !== 'undefined') {
    const el = document.getElementById('mode-error');
    if (el) {
      el.textContent = message;
      el.style.display = 'block';
    }
  }
  if (typeof console !== 'undefined') {
    // eslint-disable-next-line no-console
    console.error('[unified_root]', message);
  }
}

export function clearModeError() {
  if (typeof document === 'undefined') {
    return;
  }
  const el = document.getElementById('mode-error');
  if (el) {
    el.style.display = 'none';
    el.textContent = '';
  }
}

// ---- モード UI 反映（body クラス・リプレイトグル点灯）--------------------------
//   mode-css の付替えは不要（live/replay の app.css は同一・index.html は /live 固定）。body クラス
//   um-mode-live / um-mode-replay が replay-bar / live-follow-toggle の表示を CSS で制御する。
export function applyModeUi(mode) {
  if (typeof document === 'undefined') {
    return;
  }
  document.body.classList.toggle('um-mode-live', mode === MODE.LIVE);
  document.body.classList.toggle('um-mode-replay', mode === MODE.REPLAY);
  const replayToggle = document.getElementById('enter-replay');
  if (replayToggle) {
    replayToggle.setAttribute('aria-pressed', mode === MODE.REPLAY ? 'true' : 'false');
  }
}

// ---- リプレイ トグルボタン配線（単一 mount: DOM は永続＝1 回だけ配線）--------------
//   modeController は呼び出し側（Composition Root）が注入する（従前は module 内状態を参照）。
export function wireModeSwitchButtons(modeController) {
  const btn = document.getElementById('enter-replay');
  if (btn) {
    btn.addEventListener('click', () => {
      if (modeController) {
        modeController.toggle();
      }
    });
  }
}
