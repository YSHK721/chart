// ladder_selectors_view（adapter/front/ladder_selectors_view.js）— 第 1 表の**操作子**の版面
//   （期間ボタン 短期 / 中期 / 長期 / 全期間 ＋ 時間足ピル）。
//
// 設計入力（依頼者指示 2026-08-30）: 期間と時間足は 1 行に並べる。期間ボタンはそのグループの
//   時間足をまとめてトグルし、時間足ピルと**同一の選択集合**を操作する（フィルタ軸を 2 本に
//   しない）。外した足はトーンを外した無彩のピルにする（薄さで階層を作らない・規約 4）。
//
// なぜ表の版面から分けたのか（ISSUE-502 段階 4C・F-2）:
//   「押す道具」と「読む表」は変更要求の出所が別である（操作の追加・並べ替えは操作仕様の
//   変更、列やセルの追加は版面の変更）。同じモジュールに置くと、ボタンを 1 つ足す要求が
//   1,100 行の表モジュールの改変として現れる。DOM 構造・クラス名・生成順は分割前と同一で、
//   親（reach_sheet_view）は `.dash-ladder-selectors` を受け取って挿すだけになる。
//
// 選択の**規則**はここにも無い（domain/ladder_scope.js が唯一源）。本モジュールが持つのは
//   「押されたことを伝える」「答えを見た目へ写す」だけである。
// 切替は描き直すだけで発行を生まない（発行判定は sheet_poller の唯一責務のまま）。

import { createElementWith } from './dom_element.js';

/**
 * 操作子の版面を作る。
 *
 * @param {object}   opts
 * @param {object}   opts.doc        DOM 実装（注入）
 * @param {object}   opts.scope      表示範囲の状態機械（domain/ladder_scope.js）
 * @param {readonly string[]} opts.timeframes 時間足ピルの並び（足別トーンの添字でもある）
 * @param {Function} opts.onChange   選択が変わったときに呼ぶ（親が描き直す）
 * @returns {{build: Function, sync: Function, reset: Function}}
 */
export function createLadderSelectorsView({ doc, scope, timeframes, onChange }) {
  const el = (tag, props = {}) => createElementWith(doc, tag, props);
  let scopeButtons = [];
  let tfButtons = [];

  /** 選択が変わったときの共通処理（見た目を導き直してから親へ伝える）。 */
  function changed() {
    sync();
    if (typeof onChange === 'function') {
      onChange();
    }
  }

  /** 期間の複数選択バー（短期 / 中期 / 長期 ＋ 全期間）。 */
  function buildScopeBar() {
    const bar = el('div', { className: 'dash-ladder-scope', role: 'group' });
    scopeButtons = scope.groups.map((group) => {
      const button = el('button', {
        className: 'dash-ladder-scope-btn',
        type: 'button',
        textContent: group.label,
        dataset: { scope: group.key },
      });
      button.addEventListener('click', () => {
        scope.toggleGroup(group.key);
        changed();
      });
      bar.appendChild(button);
      return button;
    });
    const allButton = el('button', {
      className: 'dash-ladder-scope-btn',
      type: 'button',
      textContent: '全期間',
      dataset: { scope: 'all' },
    });
    allButton.addEventListener('click', () => {
      // 全期間 = 全選択＋窓なし。もう一度押すと窓ありへ戻る（選択は全選択のまま）。
      scope.toggleWindowless();
      changed();
    });
    bar.appendChild(allButton);
    scopeButtons.push(allButton);
    return bar;
  }

  /** 時間足の選択バー（トグル・既定は全選択）。見た目は行の時間足ピルと同じ語彙。 */
  function buildTfBar() {
    const bar = el('div', { className: 'dash-ladder-tf-bar', role: 'group' });
    tfButtons = timeframes.map((timeframe, tone) => {
      const button = el('button', {
        className: 'dash-ladder-tf-btn',
        type: 'button',
        dataset: { timeframe },
      });
      // 初期状態も選択状態（唯一源）から導く（再 mount しても選択が版面とずれない）。
      const initiallyOn = scope.isOn(timeframe);
      const pill = el('u', {
        className: initiallyOn ? `dash-tf-pill dash-tf-r${tone}` : 'dash-tf-pill',
        textContent: timeframe,
      });
      button.appendChild(pill);
      button.setAttribute?.('aria-pressed', String(initiallyOn));
      button.addEventListener('click', () => {
        scope.toggleTimeframe(timeframe);
        changed();
      });
      bar.appendChild(button);
      return button;
    });
    return bar;
  }

  /** 期間ボタン・全期間・時間足ピルの見た目を選択状態（唯一源）から導き直す。 */
  function sync() {
    for (const button of scopeButtons) {
      const key = button.dataset.scope;
      // 全期間ボタン（グループ名でない）は窓なしモードの真偽をそのまま映す。
      const active = key === 'all' ? scope.isWindowless() : scope.isGroupActive(key);
      button.setAttribute?.('aria-pressed', String(active));
      if (active) button.classList.add('is-active'); else button.classList.remove('is-active');
    }
    for (const button of tfButtons) {
      const timeframe = button.dataset.timeframe;
      const tone = timeframes.indexOf(timeframe);
      const on = scope.isOn(timeframe);
      button.setAttribute?.('aria-pressed', String(on));
      const pill = button.children[0];
      if (pill) {
        if (on) pill.classList.add(`dash-tf-r${tone}`);
        else pill.classList.remove(`dash-tf-r${tone}`);
      }
    }
  }

  return {
    /**
     * 操作子 1 行（期間 ＋ 時間足）を組んで返す。
     * 境界の余白は CSS（.dash-ladder-selectors の gap）が持つ＝両グループの内側より一段広い。
     */
    build() {
      const selectors = el('div', { className: 'dash-ladder-selectors' });
      selectors.appendChild(buildScopeBar());
      selectors.appendChild(buildTfBar());
      return selectors;
    },
    sync,
    /** 参照を捨てる（版面を畳むとき。選択そのものは scope 側に残す）。 */
    reset() {
      scopeButtons = [];
      tfButtons = [];
    },
  };
}
