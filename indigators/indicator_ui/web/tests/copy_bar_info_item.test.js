// copy_bar_info_item.js / clipboard_gateway.js（右クリックの「情報をコピーする」・ユーザー指示 2026-08-09）の仕様検証。
//
// 固定する規約:
//   - 項目名は「情報をコピーする」（ユーザー指示 2026-08-09 の文言）。
//   - 右クリック位置の足の情報を、凡例と同じラベルでクリップボードへ書く。
//   - 足が無い位置・書き込み失敗は**成功と区別して告知**する（無症状の失敗を作らない）。
//   - navigator.clipboard が無い配信（非 secure context）では execCommand 経路へ落ちる。
// 構造: Arrange-Act-Assert。DOM・チャート非依存（fake を注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createCopyBarInfoItem, COPY_BAR_INFO_LABEL } from '../js/adapter/front/copy_bar_info_item.js';
import { ClipboardGateway } from '../js/adapter/front/clipboard_gateway.js';

const INFO = {
  time: 1277769600,
  ohlc: { open: 1.2, high: 1.6, low: 1.1, close: 1.5 },
  sessionMP: null,
  indicators: [{ instanceId: 'rsi#1', values: [{ name: 'rsi', value: 55 }] }],
};

function fakeClipboard(ok = true) {
  return { _written: [], async writeText(t) { this._written.push(t); return ok; } };
}

function fakeToast() {
  return { _shown: [], show(t) { this._shown.push(t); } };
}

test('項目名は「情報をコピーする」', () => {
  assert.equal(COPY_BAR_INFO_LABEL, '情報をコピーする');
  assert.equal(createCopyBarInfoItem({ renderer: {}, clipboard: fakeClipboard() }).label, '情報をコピーする');
});

test('右クリック位置の足の情報を、銘柄・時間足・指標見出し付きで書き込み成功を告知する', async () => {
  // Arrange
  const seenX = [];
  const renderer = { barInfoAt(x) { seenX.push(x); return INFO; } };
  const clipboard = fakeClipboard(true);
  const toast = fakeToast();
  const item = createCopyBarInfoItem({
    renderer,
    clipboard,
    toast,
    getContext: () => ({
      symbol: 'NI225', timeframe: '1D', labels: new Map([['rsi#1', 'RSI (length=14)']]),
    }),
    now: () => 1786332341000,   // 2026-08-10 03:25:41 UTC（固定時計）
  });

  // Act
  await item.onSelect({ x: 210, y: 90 });

  // Assert
  assert.deepEqual(seenX, [210]);
  assert.deepEqual(clipboard._written, [
    'NI225\t1D\n2010-06-29 00:00\nO 1.2\tH 1.6\tL 1.1\tC 1.5\nRSI (length=14)\trsi 55'
      + '\nコピー日時\t2026-08-10 03:25:41 UTC',
  ]);
  assert.deepEqual(toast._shown, ['コピーしました']);
});

test('コピー実行時刻を最終行に添える（時計は注入・既定は実時刻）', async () => {
  // Arrange: 固定時計を注入する（実時間に依存させない）。
  const clipboard = fakeClipboard(true);
  const item = createCopyBarInfoItem({
    renderer: { barInfoAt: () => INFO },
    clipboard,
    getContext: () => ({ symbol: 'NI225', timeframe: '1D' }),
    now: () => 1786332341000,   // 2026-08-10 03:25:41 UTC
  });

  // Act
  await item.onSelect({ x: 1 });

  // Assert
  assert.match(clipboard._written[0], /\nコピー日時\t2026-08-10 03:25:41 UTC$/);
});

test('時計未注入でも書き込みは成立する（既定の実時刻が入る）', async () => {
  const clipboard = fakeClipboard(true);
  const item = createCopyBarInfoItem({ renderer: { barInfoAt: () => INFO }, clipboard });
  await item.onSelect({ x: 1 });
  assert.match(clipboard._written[0], /\nコピー日時\t\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$/);
});

test('文脈提供が無くても値はコピーする（縮退・例外にしない）', async () => {
  const clipboard = fakeClipboard(true);
  const item = createCopyBarInfoItem({ renderer: { barInfoAt: () => INFO }, clipboard });
  await item.onSelect({ x: 1 });
  assert.match(clipboard._written[0], /^2010-06-29 00:00\n/);
});

test('足が無い位置はコピーせず、その旨を告知する', async () => {
  const clipboard = fakeClipboard(true);
  const toast = fakeToast();
  const item = createCopyBarInfoItem({ renderer: { barInfoAt: () => null }, clipboard, toast });
  await item.onSelect({ x: 5 });
  assert.deepEqual(clipboard._written, []);
  assert.deepEqual(toast._shown, ['この位置に足がありません']);
});

test('書き込み失敗は成功と区別して告知する（無症状の失敗を作らない）', async () => {
  const toast = fakeToast();
  const item = createCopyBarInfoItem({
    renderer: { barInfoAt: () => INFO }, clipboard: fakeClipboard(false), toast,
  });
  await item.onSelect({ x: 1 });
  assert.deepEqual(toast._shown, ['コピーできませんでした']);
});

test('toast 未注入・renderer 未対応でも落ちない', async () => {
  const item = createCopyBarInfoItem({ renderer: {}, clipboard: fakeClipboard(true) });
  await item.onSelect({ x: 1 });
});

// ---------------------------------------------------------------------------
// ClipboardGateway
// ---------------------------------------------------------------------------

test('ClipboardGateway: navigator.clipboard があればそれを使う', async () => {
  const written = [];
  const gw = new ClipboardGateway({ navigator: { clipboard: { async writeText(t) { written.push(t); } } } });
  assert.equal(await gw.writeText('abc'), true);
  assert.deepEqual(written, ['abc']);
});

test('ClipboardGateway: 非 secure context（clipboard 無し）は execCommand 経路へ落ちる', async () => {
  const created = [];
  const body = { children: [], appendChild(n) { this.children.push(n); }, removeChild(n) { this.children = this.children.filter((x) => x !== n); } };
  const doc = {
    body,
    _copied: false,
    createElement() {
      const el = { value: '', style: {}, setAttribute() {}, select() { el._selected = true; } };
      created.push(el);
      return el;
    },
    execCommand(cmd) { doc._copied = cmd === 'copy'; return true; },
  };
  const gw = new ClipboardGateway({ navigator: {}, document: doc });

  assert.equal(await gw.writeText('abc'), true);
  assert.equal(created[0].value, 'abc');
  assert.equal(created[0]._selected, true);
  assert.equal(doc._copied, true);
  assert.deepEqual(body.children, []);   // 一時要素を残さない
});

test('ClipboardGateway: clipboard 失敗時も execCommand 経路を試す', async () => {
  const doc = {
    body: { children: [], appendChild(n) { this.children.push(n); }, removeChild() {} },
    createElement: () => ({ value: '', style: {}, setAttribute() {}, select() {} }),
    execCommand: () => true,
  };
  const gw = new ClipboardGateway({
    navigator: { clipboard: { writeText: async () => { throw new Error('denied'); } } }, document: doc,
  });
  assert.equal(await gw.writeText('abc'), true);
});

test('ClipboardGateway: どちらも使えない環境・空文字は false（成功を偽らない）', async () => {
  const gw = new ClipboardGateway({ navigator: {}, document: null });
  assert.equal(await gw.writeText('abc'), false);
  assert.equal(await new ClipboardGateway({ navigator: {}, document: null }).writeText(''), false);
});
