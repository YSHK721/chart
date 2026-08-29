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

import {
  MODES,
  MODE_TOGGLE_BUTTONS,
  DEFAULT_MODE,
  bodyClassOf,
  hasChartApi,
  CHART_API_BODY_CLASS,
  usesBottomPane,
  BOTTOM_PANE_BODY_CLASS,
} from './mode_table.js';

// モード列挙。定義表から導出する（第 2 の定義を持たない＝表へ 1 行足せば列挙も増える）。
//   キーは従来どおり大文字（MODE.LIVE / MODE.REPLAY・公開 API 不変）で、SIM が加わる。
export const MODE = Object.freeze(
  Object.fromEntries(MODES.map((m) => [m.id.toUpperCase(), m.id])),
);

// ボタン id → 目標モードの対応表（§11.2 L-3）。トグルは表から、リプレイバー右端の ✕ は
//   「リプレイ終了＝既定モードへ戻る」という固有の意味を持つのでここで 1 行だけ足す。
//   3 値以上では「反転」が定義できないため、いずれも **明示ターゲット** で toggle を呼ぶ。
const MODE_SWITCH_BUTTONS = Object.freeze([
  ...MODE_TOGGLE_BUTTONS.map((b) => Object.freeze({ id: b.id, mode: b.mode })),
  Object.freeze({ id: 'rp-close', mode: DEFAULT_MODE }),
]);

let lwcLoaded = false;

// ---- lightweight-charts（vendor）を live prefix から動的ロード（両 core とも同一 vendor を配信）----
//   読込手順そのものは core の共有モジュール（vendor_loader）が所有する（ISSUE-278 #11）。
//   以前はここだけ `onerror → resolve(false)` の 1 回勝負で、ISSUE-166 の承認済み再試行
//   （cache-bust つき最大 3 回）が**実際に配信されるページからだけ抜けていた**。配信の途中切断で
//   起動を諦め、F5 まで回復しない状態になる。手順を 1 か所に置き、3 ページとも同じ防御にする。
export async function loadVendor(mode) {
  if (lwcLoaded || (typeof window !== 'undefined' && window.LightweightCharts)) {
    lwcLoaded = true;
    return true;
  }
  // URL は変数へ束縛してから import する（テンプレートを import() 直下へ書くとバンドラの
  //   静的解析が警告を出す。実行は素の ESM なのでどちらでも動くが、警告を残さない）。
  const loaderUrl = `/${mode}/js/adapter/front/vendor_loader.js`;
  const { ensureLightweightCharts } = await import(loaderUrl);
  const lwc = await ensureLightweightCharts({ src: `/${mode}/vendor/lightweight-charts.js` });
  lwcLoaded = !!lwc;
  return lwcLoaded;
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
  // body クラスは表を走査して相互排他に切替える（クラスを 1 つずつ toggle する形で書くと、
  //   モードを増やすたびに行を足す義務が生まれる＝OCP 違反・§3.5.6 #2）。
  const active = bodyClassOf(mode);
  for (const row of MODES) {
    document.body.classList.toggle(row.bodyClass, row.bodyClass === active);
  }
  // 「この core はチャート API を持つ」状態も body へ出す（🟡-5）。CSS はモード名ではなく
  //   本クラスの有無だけを見るので、モードを増やしても CSS 側は変わらない。
  document.body.classList.toggle(CHART_API_BODY_CLASS, hasChartApi(mode));
  // 「このモードの表示層は下部ドックペインを使う」も同形で body へ出す（ISSUE-460）。
  //   CSS がモード名を列挙せずに済む＝空のペインが第 4 モードで出る欠陥を表駆動で消す。
  document.body.classList.toggle(BOTTOM_PANE_BODY_CLASS, usesBottomPane(mode));
  // 各トグルの点灯（aria-pressed）も表から。自分のモードのときだけ true、他は false。
  for (const btn of MODE_TOGGLE_BUTTONS) {
    const el = document.getElementById(btn.id);
    if (el) {
      el.setAttribute('aria-pressed', btn.mode === mode ? 'true' : 'false');
    }
  }
}

// ---- リプレイ トグルボタン配線（単一 mount: DOM は永続＝1 回だけ配線）--------------
//   modeController は呼び出し側（Composition Root）が注入する（従前は module 内状態を参照）。
export function wireModeSwitchButtons(modeController) {
  // 各ボタンは **目標モードを明示して** toggle を呼ぶ（§11.2 L-3）。引数なしの `toggle()`
  //   （＝2 値反転）では 3 値以上で行き先が定義できない。
  //   リプレイバー右端の ✕ は「リプレイ終了＝既定モードへ戻る」を意味する（表の最終行）。
  //
  // 行き先の解決（🔴-1 の是正）: ボタンは**オン・オフのトグル**である。目標モードを固定で
  //   渡すと、そのモードが既にアクティブなときに `toggle` の同一モードガード
  //   （`target === activeMode` で return）へ当たり、押しても何も起きない。develop では
  //   2 値反転がオフ動作を担っていたため、明示指定化でその動作が失われていた
  //   （enter-replay がオフを失い、sim は enter-sim でモードを抜けられない）。
  //   よって押下時に現在モードを読み、自分のモードがアクティブなら既定モードへ戻す。
  //   これは develop の 2 値反転を 3 値以上へ一般化したもので、モード名は定義表から来る。
  const resolveTarget = (mode) => {
    const cur = modeController && typeof modeController.getMode === 'function'
      ? modeController.getMode()
      : undefined;                  // getMode 未実装の注入は従来どおりオン動作のみ（後方互換）。
    return cur === mode ? DEFAULT_MODE : mode;
  };
  for (const { id, mode } of MODE_SWITCH_BUTTONS) {
    const btn = document.getElementById(id);
    if (btn) {
      btn.addEventListener('click', () => {
        if (modeController) {
          modeController.toggle(resolveTarget(mode));
        }
      });
    }
  }
}
