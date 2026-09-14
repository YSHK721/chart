// replay_range_menu.js — リプレイバー左端「[ 期間 ] [∨]」ドロップダウンの DOM アダプタ。
//
// 仕様（依頼者確定 2026-07-26）:
//   [∨] クリックで [ カレンダー ] or [ 期間プリセット ] を選ぶ。
//   - 期間プリセット: 時間足別の候補（例 日足 = 3か月 / 6か月 / 1年 / 全期間）。現在の present から遡る。
//   - カレンダー:     再生開始日を選ぶ。データが無い日はグレーアウトで選択できない。
//
// 純ロジック（月グリッド・日キー）は replay/calendar.js。本モジュールは DOM 副作用のみを持つ。

import { ReplayPopup } from './replay_popup.js';
import { monthCells, shiftMonth, latestMonth, dayStartUnix } from '../../replay/calendar.js';

const WEEK_LABELS = ['日', '月', '火', '水', '木', '金', '土'];

export class ReplayRangeMenu {
  /**
   * @param {object} o
   * @param {Document} o.document
   * @param {() => Promise<string[]>} o.loadDays 選択可能日（"YYYY-MM-DD" 昇順）を返す（遅延取得）
   * @param {(secs: number|null) => void} o.onSelectPreset 期間プリセット選択（秒・null=全期間）
   * @param {(startUnix: number, key: string) => void} o.onSelectDate カレンダーの日選択（UTC 日の 00:00）
   */
  constructor({ document: doc, loadDays, onSelectPreset, onSelectDate }) {
    this._doc = doc;
    this._loadDays = loadDays;
    this._onSelectPreset = onSelectPreset;
    this._onSelectDate = onSelectDate;
    this._presets = [['全期間', null]];
    this._days = null;      // 取得済みの選択可能日（Set）。未取得は null。
    this._month = null;     // カレンダーの表示月 {year, month}
    this._pop = new ReplayPopup({ document: doc });
  }

  setPresets(presets) { this._presets = presets && presets.length ? presets : [['全期間', null]]; }

  // 時間足が変わったら選択可能日は取り直す（足の存在日が変わるため）。
  invalidateDays() { this._days = null; this._month = null; }

  isOpen() { return this._pop.isOpen(); }
  close() { this._pop.close(); }

  toggle(anchor) {
    if (this._pop.isOpen()) { this._pop.close(); return; }
    this._renderRoot();
    this._pop.open(anchor);
  }

  // ---- 描画 ---- //

  _button(label, className, onClick) {
    const b = this._doc.createElement('button');
    b.type = 'button';
    b.className = className;
    b.textContent = label;
    b.addEventListener('click', onClick);
    return b;
  }

  _renderRoot() {
    this._pop.clear();
    const anchor = this._doc.getElementById('rp-range-caret');
    this._pop.body.appendChild(this._button('カレンダー', 'rp-pop-item', () => {
      this._renderCalendar().then(() => this._pop.open(anchor));
    }));
    this._pop.body.appendChild(this._button('期間プリセット', 'rp-pop-item', () => {
      this._renderPresets();
      this._pop.open(anchor);
    }));
  }

  _renderPresets() {
    this._pop.clear();
    for (const [label, secs] of this._presets) {
      this._pop.body.appendChild(this._button(label, 'rp-pop-item', () => {
        this._pop.close();
        this._onSelectPreset(secs);
      }));
    }
  }

  async _renderCalendar() {
    if (this._days == null) {
      let days = [];
      try { days = await this._loadDays(); } catch (_e) { days = []; }
      this._days = new Set(days);
      this._month = latestMonth(days);
    }
    if (!this._month) {
      this._pop.clear();
      const msg = this._doc.createElement('div');
      msg.className = 'rp-pop-msg';
      msg.textContent = '選択できる日がありません';
      this._pop.body.appendChild(msg);
      return;
    }
    this._paintCalendar();
  }

  _paintCalendar() {
    this._pop.clear();
    const cal = this._doc.createElement('div');
    cal.className = 'rp-cal';

    const head = this._doc.createElement('div');
    head.className = 'rp-cal-head';
    head.appendChild(this._button('‹', 'rp-cal-nav', () => {
      this._month = shiftMonth(this._month, -1);
      this._paintCalendar();
      this._pop.open(this._doc.getElementById('rp-range-caret'));
    }));
    const title = this._doc.createElement('span');
    title.className = 'rp-cal-title';
    title.textContent = `${this._month.year}年 ${this._month.month}月`;
    head.appendChild(title);
    head.appendChild(this._button('›', 'rp-cal-nav', () => {
      this._month = shiftMonth(this._month, 1);
      this._paintCalendar();
      this._pop.open(this._doc.getElementById('rp-range-caret'));
    }));
    cal.appendChild(head);

    const grid = this._doc.createElement('div');
    grid.className = 'rp-cal-grid';
    for (const w of WEEK_LABELS) {
      const c = this._doc.createElement('span');
      c.className = 'rp-cal-w';
      c.textContent = w;
      grid.appendChild(c);
    }
    for (const cell of monthCells(this._month)) {
      const usable = cell.inMonth && this._days.has(cell.key);
      const b = this._doc.createElement('button');
      b.type = 'button';
      b.className = 'rp-cal-day' + (usable ? '' : ' off') + (cell.inMonth ? '' : ' out');
      b.textContent = String(cell.day);
      // データが無い日はグレーアウトで選択できない（クリックも受けない）。
      if (usable) {
        b.addEventListener('click', () => {
          this._pop.close();
          this._onSelectDate(dayStartUnix(cell.key), cell.key);
        });
      } else {
        b.disabled = true;
      }
      grid.appendChild(b);
    }
    cal.appendChild(grid);
    this._pop.body.appendChild(cal);
  }

  destroy() { this._pop.destroy(); }
}
