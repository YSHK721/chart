// replay_speed_menu.js — リプレイバー「x1.00」ドロップダウンの DOM アダプタ。
//
// 仕様（依頼者確定 2026-07-26）: 速度プリセット [x1.00 / x0.75 / x0.50 / x0.25 / x0.01]
//   ＋ 自由入力 0.00〜1.00。値の意味（1.00=最速・0.00=一時停止＝凍結）は従来と同一で、
//   replay/timing.js の clampSpeed が唯一の値域権威（本モジュールは入力を渡すだけ）。
// 追加（依頼者指示 2026-08-01）: 末尾に「リアルタイム」＝市場時刻を実時間で流すテンポ（比でない別軸）。

import { ReplayPopup } from './replay_popup.js';
import { REALTIME, isRealtime } from '../../replay/timing.js';

export const SPEED_PRESETS = [1, 0.75, 0.5, 0.25, 0.01, REALTIME];

const presetLabel = (v) => (isRealtime(v) ? 'リアルタイム' : `x${v.toFixed(2)}`);
const presetOn = (v, cur) => (isRealtime(v)
  ? isRealtime(cur)
  : !isRealtime(cur) && Math.abs(v - cur) < 1e-9);

export class ReplaySpeedMenu {
  /**
   * @param {object} o
   * @param {Document} o.document
   * @param {() => number} o.readSpeed 現在速度（点灯表示用）
   * @param {(v: number) => void} o.onSelect 速度確定
   */
  constructor({ document: doc, readSpeed, onSelect }) {
    this._doc = doc;
    this._readSpeed = readSpeed;
    this._onSelect = onSelect;
    this._pop = new ReplayPopup({ document: doc });
  }

  isOpen() { return this._pop.isOpen(); }
  close() { this._pop.close(); }

  toggle(anchor) {
    if (this._pop.isOpen()) { this._pop.close(); return; }
    this._render();
    this._pop.open(anchor);
  }

  _render() {
    this._pop.clear();
    const cur = this._readSpeed();
    for (const v of SPEED_PRESETS) {
      const b = this._doc.createElement('button');
      b.type = 'button';
      b.className = 'rp-pop-item' + (presetOn(v, cur) ? ' on' : '');
      b.textContent = presetLabel(v);
      if (isRealtime(v)) b.title = '市場時刻を実時間で流す（1足の所要＝時間足の長さ）';
      b.addEventListener('click', () => { this._pop.close(); this._onSelect(v); });
      this._pop.body.appendChild(b);
    }
    // 自由入力（0.00〜1.00）。Enter か blur で確定する。
    const wrap = this._doc.createElement('div');
    wrap.className = 'rp-pop-input';
    const input = this._doc.createElement('input');
    input.type = 'number';
    input.min = '0';
    input.max = '1';
    input.step = '0.01';
    input.value = isRealtime(cur) ? '' : cur.toFixed(2);
    input.title = '自由入力（0.00〜1.00・0.00=一時停止）';
    const commit = () => {
      const v = parseFloat(input.value);
      if (!Number.isFinite(v)) return;
      this._pop.close();
      this._onSelect(v);
    };
    input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') commit(); });
    input.addEventListener('change', commit);
    wrap.appendChild(input);
    this._pop.body.appendChild(wrap);
  }

  destroy() { this._pop.destroy(); }
}
