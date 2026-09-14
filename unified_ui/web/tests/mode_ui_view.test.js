// mode_ui_view（UI/DOM アダプタ）の契約テスト。
//
// 保証対象: unified_root.js から抽出した MODE / loadVendor / showModeError / clearModeError /
//   applyModeUi / wireModeSwitchButtons の移設後契約を固定する（DOM id・body クラス・aria 属性・
//   modeController 注入によるトグル配線が不変であること）。
// 環境は node（DOM なし）。document / window はテスト内で最小 fake に差し替える（vi.stubGlobal）。
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect, vi, afterEach } from 'vitest';
import {
  MODE,
  loadVendor,
  showModeError,
  clearModeError,
  applyModeUi,
  wireModeSwitchButtons,
} from '../js/mode_ui_view.js';
// モード集合・body クラス・トグル id の単一ソース（§3.5.6 の表駆動化）。
import {
  MODE_IDS, MODE_BODY_CLASSES, MODE_TOGGLE_BUTTONS, bodyClassOf, DEFAULT_MODE,
  hasChartApi, CHART_API_BODY_CLASS,
} from '../js/mode_table.js';

// classList.toggle(cls, force) を Set で観測できる最小 body fake。
function fakeBody() {
  const classes = new Set();
  return {
    classes,
    classList: {
      toggle: (cls, force) => {
        if (force) {
          classes.add(cls);
        } else {
          classes.delete(cls);
        }
      },
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('MODE — モード列挙', () => {
  test('値がliveとreplayでfrozen', () => {
    // Assert
    expect(MODE.LIVE).toBe('live');
    expect(MODE.REPLAY).toBe('replay');
    expect(Object.isFrozen(MODE)).toBe(true);
  });

  test('SIM_第3モードが列挙に載る（§3.5.6 #1）', () => {
    // Assert
    expect(MODE.SIM).toBe('sim');
  });

  test('列挙はモード定義表から導出される（第2の定義を持たない）', () => {
    // Assert: 表に載っている全モードが列挙に現れ、列挙は表以外の値を持たない。
    expect(Object.values(MODE).sort()).toEqual([...MODE_IDS].sort());
  });
});

describe('loadVendor — vendor 動的ロード', () => {
  test('LightweightCharts既存_scriptを生成せずtrueをresolveする', async () => {
    // Arrange: window.LightweightCharts がある短絡経路（document には触れない）。
    vi.stubGlobal('window', { LightweightCharts: {} });
    // Act
    const ok = await loadVendor('live');
    // Assert
    expect(ok).toBe(true);
  });
});

describe('showModeError / clearModeError — エラー表示', () => {
  test('showModeError_mode-error要素にメッセージを表示する', () => {
    // Arrange
    const el = { textContent: '', style: { display: '' } };
    vi.stubGlobal('document', { getElementById: (id) => (id === 'mode-error' ? el : null) });
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    // Act
    showModeError('boom');
    // Assert
    expect(el.textContent).toBe('boom');
    expect(el.style.display).toBe('block');
    expect(spy).toHaveBeenCalledWith('[unified_root]', 'boom');
    spy.mockRestore();
  });

  test('clearModeError_mode-error要素を非表示にしテキストを空にする', () => {
    // Arrange
    const el = { textContent: 'prev', style: { display: 'block' } };
    vi.stubGlobal('document', { getElementById: (id) => (id === 'mode-error' ? el : null) });
    // Act
    clearModeError();
    // Assert
    expect(el.style.display).toBe('none');
    expect(el.textContent).toBe('');
  });
});

describe('applyModeUi — モード UI 反映', () => {
  test('replay_body_umモードクラスとaria-pressedを切替える', () => {
    // Arrange
    const body = fakeBody();
    const toggle = { attrs: {}, setAttribute: (k, v) => { toggle.attrs[k] = v; } };
    vi.stubGlobal('document', {
      body,
      getElementById: (id) => (id === 'enter-replay' ? toggle : null),
    });
    // Act
    applyModeUi(MODE.REPLAY);
    // Assert
    expect(body.classes.has('um-mode-replay')).toBe(true);
    expect(body.classes.has('um-mode-live')).toBe(false);
    expect(toggle.attrs['aria-pressed']).toBe('true');
  });

  test('live_body_umモードクラスとaria-pressedを切替える', () => {
    // Arrange
    const body = fakeBody();
    const toggle = { attrs: {}, setAttribute: (k, v) => { toggle.attrs[k] = v; } };
    vi.stubGlobal('document', {
      body,
      getElementById: (id) => (id === 'enter-replay' ? toggle : null),
    });
    // Act
    applyModeUi(MODE.LIVE);
    // Assert
    expect(body.classes.has('um-mode-live')).toBe(true);
    expect(body.classes.has('um-mode-replay')).toBe(false);
    expect(toggle.attrs['aria-pressed']).toBe('false');
  });
});

describe('applyModeUi — 3 値化（表走査・§3.5.6 #2）', () => {
  // 表に載っている全トグルを観測できる document fake。
  function fakeDoc() {
    const body = fakeBody();
    const toggles = new Map();
    for (const b of MODE_TOGGLE_BUTTONS) {
      toggles.set(b.id, { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } });
    }
    return {
      body,
      toggles,
      getElementById: (id) => toggles.get(id) || null,
    };
  }

  test('sim_um-mode-simのみが付き他のモードクラスは外れる（相互排他）', () => {
    // Arrange
    const doc = fakeDoc();
    vi.stubGlobal('document', doc);
    // Act
    applyModeUi('sim');
    // Assert
    expect(doc.body.classes.has('um-mode-sim')).toBe(true);
    expect(doc.body.classes.has('um-mode-live')).toBe(false);
    expect(doc.body.classes.has('um-mode-replay')).toBe(false);
  });

  test('各モードで表の全クラスが相互排他に切替わる（第4モードも自動で覆う）', () => {
    for (const id of MODE_IDS) {
      // Arrange
      const doc = fakeDoc();
      vi.stubGlobal('document', doc);
      // Act
      applyModeUi(id);
      // Assert
      const on = bodyClassOf(id);
      for (const cls of MODE_BODY_CLASSES) {
        expect(doc.body.classes.has(cls)).toBe(cls === on);
      }
    }
  });

  test('chartApiを持つモードでのみum-chart-apiが付く（🟡-5・状態クラスの反映）', () => {
    for (const id of MODE_IDS) {
      // Arrange
      const doc = fakeDoc();
      vi.stubGlobal('document', doc);
      // Act
      applyModeUi(id);
      // Assert: CSS はこのクラスの有無だけを見る（モード名を知らない＝第 4 モードで CSS 不変）。
      expect(doc.body.classes.has(CHART_API_BODY_CLASS)).toBe(hasChartApi(id));
    }
  });

  test('各トグルのaria-pressedは自分のモードのときだけtrue', () => {
    for (const id of MODE_IDS) {
      // Arrange
      const doc = fakeDoc();
      vi.stubGlobal('document', doc);
      // Act
      applyModeUi(id);
      // Assert
      for (const b of MODE_TOGGLE_BUTTONS) {
        expect(doc.toggles.get(b.id).attrs['aria-pressed']).toBe(b.mode === id ? 'true' : 'false');
      }
    }
  });
});

describe('wireModeSwitchButtons — トグルボタン配線（modeController 注入）', () => {
  test('click_注入したmodeControllerのtoggleを呼ぶ', () => {
    // Arrange
    let handler = null;
    const btn = { addEventListener: (ev, fn) => { if (ev === 'click') handler = fn; } };
    vi.stubGlobal('document', { getElementById: (id) => (id === 'enter-replay' ? btn : null) });
    let toggled = 0;
    const modeController = { toggle: () => { toggled += 1; } };
    // Act
    wireModeSwitchButtons(modeController);
    handler();
    // Assert
    expect(toggled).toBe(1);
  });

  // --- L-3: ボタン id → 目標モードの表から toggle(target) を**明示指定**する -------------
  //
  // 旧実装は id 集合 ['enter-replay','rp-close'] をハードコードし、いずれも引数なしの
  //   `toggle()`（＝2 値反転）を呼んでいた。3 値では「反転」が定義できず、sim ボタンを足しても
  //   どのモードへ行くのか表現できない。id→目標モードの対応を表から取り、明示指定へ変える。
  test('enter-sim_clickでtoggleにsimが明示指定される', () => {
    // Arrange
    const handlers = new Map();
    const makeBtn = (id) => ({ addEventListener: (ev, fn) => { if (ev === 'click') handlers.set(id, fn); } });
    vi.stubGlobal('document', { getElementById: (id) => makeBtn(id) });
    const targets = [];
    // Act
    wireModeSwitchButtons({ toggle: (t) => targets.push(t) });
    handlers.get('enter-sim')();
    // Assert
    expect(targets).toEqual(['sim']);
  });

  test('表の各トグルボタンは自分のモードをtoggleへ渡す（第4モードも自動で覆う）', () => {
    // Arrange
    const handlers = new Map();
    vi.stubGlobal('document', {
      getElementById: (id) => ({ addEventListener: (ev, fn) => { if (ev === 'click') handlers.set(id, fn); } }),
    });
    const targets = [];
    // Act
    wireModeSwitchButtons({ toggle: (t) => targets.push(t) });
    for (const b of MODE_TOGGLE_BUTTONS) {
      handlers.get(b.id)();
    }
    // Assert
    expect(targets).toEqual(MODE_TOGGLE_BUTTONS.map((b) => b.mode));
  });

  test('rp-close_clickは既定モード（ライブ）を明示指定する', () => {
    // Arrange: リプレイバー右端の ✕ は「リプレイ終了＝ライブへ戻る」。3 値では反転で表せない。
    const handlers = new Map();
    vi.stubGlobal('document', {
      getElementById: (id) => ({ addEventListener: (ev, fn) => { if (ev === 'click') handlers.set(id, fn); } }),
    });
    const targets = [];
    // Act
    wireModeSwitchButtons({ toggle: (t) => targets.push(t) });
    handlers.get('rp-close')();
    // Assert
    expect(targets).toEqual([DEFAULT_MODE]);
  });

  // --- 🔴-1: トグルの「オフ」動作（アクティブなモードの再押下）------------------------
  //
  // 回帰の実態: develop の `wireModeSwitchButtons` は引数なしの `toggle()` を呼び、当時の
  //   `toggle` が 2 値反転していたため「replay 中に enter-replay を押す＝live へ戻る」が成立して
  //   いた。L-3 で目標モードを明示指定（`toggle(mode)`）にした結果、`toggle` の同一モードガード
  //   （`target === activeMode` で return）に当たって**押しても何も起きなく**なった。
  //   enter-replay はオフ動作を失い（develop からの回帰）、sim は enter-sim でモードを抜けられない。
  //
  // 是正: ボタンは「自分のモードが今アクティブか」を見て行き先を決める。
  //   アクティブなら既定モードへ戻す（オフ）、そうでなければ自分のモードへ入る（オン）。
  //   これは develop の 2 値反転を 3 値以上へ一般化したもので、モード名は表から来る。
  function wireWith(currentMode) {
    const handlers = new Map();
    vi.stubGlobal('document', {
      getElementById: (id) => ({ addEventListener: (ev, fn) => { if (ev === 'click') handlers.set(id, fn); } }),
    });
    const targets = [];
    wireModeSwitchButtons({ toggle: (t) => targets.push(t), getMode: () => currentMode });
    return { handlers, targets };
  }

  test('replay中にenter-replay再押下_既定モード（ライブ）へ戻る', () => {
    // Arrange
    const { handlers, targets } = wireWith('replay');
    // Act
    handlers.get('enter-replay')();
    // Assert
    expect(targets).toEqual([DEFAULT_MODE]);
  });

  test('sim中にenter-sim再押下_既定モード（ライブ）へ戻る', () => {
    // Arrange
    const { handlers, targets } = wireWith('sim');
    // Act
    handlers.get('enter-sim')();
    // Assert
    expect(targets).toEqual([DEFAULT_MODE]);
  });

  test('非アクティブなモードのボタン押下_そのモードへ入る（オン動作は不変）', () => {
    // Arrange: sim 中に enter-replay を押す＝replay へ入る（既定へ戻すのではない）。
    const { handlers, targets } = wireWith('sim');
    // Act
    handlers.get('enter-replay')();
    // Assert
    expect(targets).toEqual(['replay']);
  });

  test('表の全トグルが_アクティブ時オフ_非アクティブ時オンに解決する（第4モードも自動で覆う）', () => {
    for (const b of MODE_TOGGLE_BUTTONS) {
      // Arrange / Act: 自分のモードがアクティブなとき
      const active = wireWith(b.mode);
      active.handlers.get(b.id)();
      // Assert
      expect(active.targets).toEqual([DEFAULT_MODE]);
      // Arrange / Act: 既定モードに居るとき
      const off = wireWith(DEFAULT_MODE);
      off.handlers.get(b.id)();
      // Assert
      expect(off.targets).toEqual([b.mode]);
    }
  });

  test('getModeを持たないmodeController_従来どおり自分のモードを渡す（後方互換）', () => {
    // Arrange: getMode 未実装の注入（既存テストが使う形）でも例外にせず、オン動作だけ行う。
    const handlers = new Map();
    vi.stubGlobal('document', {
      getElementById: (id) => ({ addEventListener: (ev, fn) => { if (ev === 'click') handlers.set(id, fn); } }),
    });
    const targets = [];
    // Act
    wireModeSwitchButtons({ toggle: (t) => targets.push(t) });
    handlers.get('enter-sim')();
    // Assert
    expect(targets).toEqual(['sim']);
  });

  test('modeController_null_clickでも例外を投げない', () => {
    // Arrange
    let handler = null;
    const btn = { addEventListener: (ev, fn) => { if (ev === 'click') handler = fn; } };
    vi.stubGlobal('document', { getElementById: (id) => (id === 'enter-replay' ? btn : null) });
    // Act
    wireModeSwitchButtons(null);
    // Assert
    expect(() => handler()).not.toThrow();
  });
});
