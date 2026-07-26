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
