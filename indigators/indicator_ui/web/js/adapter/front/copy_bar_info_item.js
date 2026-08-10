// copy_bar_info_item.js — 右クリックメニューの「情報をコピーする」項目（ユーザー指示 2026-08-09）。
//
// 設計入力: ユーザー指示（2026-08-09）「日付・時間・四本値・指標をクリップボードへコピー」。
//
// 責務（SRP）: 3 つの協働子を繋ぐだけ。
//   1. 足の情報を取る   … renderer.barInfoAt(x)（upstream 隔離は renderer 側）
//   2. 文字列にする     … formatBarInfoText（純関数）
//   3. クリップボードへ … clipboard.writeText（ポート）
//   結果の告知は toast（省略可）。
//
// メニュー（ChartContextMenu）へは { label, onSelect } の 1 項目として渡す。メニューは
//   項目の中身を知らず、本モジュールはメニューの開閉を知らない（項目追加＝メニュー無改変・OCP）。

import { formatBarInfoText } from './bar_info_text.js';

export const COPY_BAR_INFO_LABEL = '情報をコピーする';

const MSG_OK = 'コピーしました';
const MSG_NG = 'コピーできませんでした';
const MSG_EMPTY = 'この位置に足がありません';

/**
 * @param {object} deps
 * @param {object} deps.renderer      ChartRenderer（barInfoAt(x) を持つ）。
 * @param {Function} [deps.getContext] コピー時点のチャート文脈 { symbol, timeframe, labels } を返す
 *                                     （銘柄・時間足・指標の見出し）。既定 null＝文脈なしで値だけ。
 * @param {object} deps.clipboard     ClipboardGateway 互換（writeText(text): Promise<boolean>）。
 * @param {object} [deps.toast]       ChartToastView 互換（show(text)）。未注入なら告知しない。
 * @param {Function} [deps.now]       実時刻の供給（epoch ミリ秒）。既定は Date.now。
 *                                    時計を注入で持つのは、整形側を純関数のまま保ち、検定で
 *                                    「いつコピーしたか」を固定値で検証できるようにするため。
 * @returns {{label: string, onSelect: Function}} ChartContextMenu へ渡す項目。
 */
export function createCopyBarInfoItem({
  renderer, getContext = null, clipboard, toast = null, now = () => Date.now(),
} = {}) {
  const notify = (msg) => {
    if (toast && typeof toast.show === 'function') {
      toast.show(msg);
    }
  };
  return {
    label: COPY_BAR_INFO_LABEL,
    onSelect: async (context) => {
      const x = context ? context.x : undefined;
      const info = (renderer && typeof renderer.barInfoAt === 'function') ? renderer.barInfoAt(x) : null;
      // 右クリック位置（context）とチャート文脈（chartContext）は別物。名前を分けて取り違えない。
      const chartContext = (typeof getContext === 'function' ? getContext() : null) || {};
      const copiedAtMs = typeof now === 'function' ? now() : null;
      const text = formatBarInfoText(info, { ...chartContext, copiedAtMs });
      if (!text) {
        notify(MSG_EMPTY);   // 足の無い位置（データ範囲外）。成功したふりをしない。
        return;
      }
      const ok = clipboard && typeof clipboard.writeText === 'function'
        ? await clipboard.writeText(text) : false;
      notify(ok ? MSG_OK : MSG_NG);
    },
  };
}
