// bar_info_text.js（足 1 本のコピー用テキスト整形）の仕様検証。
//
// 設計入力: ユーザー指示（2026-08-09）「情報ウィンドの価格情報や指標の値を一括コピー。
//   日付・時間・四本値・指標をクリップボードへ」。
// 規約の要点（本検定が固定するもの）:
//   - 表記は情報ウィンドと同じ材料・同じ並び（日時 → 四本値 → 当日 MP → 指標）。
//   - 指標行のラベルは凡例と同じ（instanceId ではない）。
//   - その足に値が無い系列は出さない（空欄・0 を捏造しない）。
// 構造: Arrange-Act-Assert。純関数のため DOM・チャート非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { formatBarInfoText } from '../js/adapter/front/bar_info_text.js';

const INFO = {
  time: 1277769600,          // 2010-06-29 00:00 UTC
  ohlc: { open: 1.2, high: 1.6, low: 1.1, close: 1.5 },
  sessionMP: null,
  indicators: [
    { instanceId: 'rsi#1', values: [{ name: 'rsi', value: 55.25 }] },
  ],
};

const LABELS = new Map([['rsi#1', 'RSI']]);

test('日時・四本値・指標を情報ウィンドと同じ並びで返す', () => {
  const text = formatBarInfoText(INFO, LABELS);
  assert.deepEqual(text.split('\n'), [
    '2010-06-29 00:00',
    'O 1.2\tH 1.6\tL 1.1\tC 1.5',
    'RSI\trsi 55.25',
  ]);
});

test('指標行のラベルは凡例のラベル（未知 instanceId は instanceId 表記へ縮退）', () => {
  const text = formatBarInfoText(INFO, new Map());
  assert.match(text, /^rsi#1\trsi 55\.25$/m);
});

test('labels 未注入でも落ちない（instanceId 表記）', () => {
  const text = formatBarInfoText(INFO);
  assert.match(text, /^rsi#1\t/m);
});

test('その足に値が無い系列は出さない・全系列が無い指標は行ごと出さない', () => {
  const info = {
    ...INFO,
    indicators: [
      { instanceId: 'ma#1', values: [{ name: 'ma20', value: 10 }, { name: 'ma50', value: undefined }] },
      { instanceId: 'rsi#1', values: [{ name: 'rsi', value: null }] },
    ],
  };
  const lines = formatBarInfoText(info, new Map([['ma#1', 'MA'], ['rsi#1', 'RSI']])).split('\n');
  assert.deepEqual(lines[2], 'MA\tma20 10');
  assert.equal(lines.length, 3);   // RSI の行は作られない
});

test('当日 MP（POC/VA）は DTO に載っているときだけ四本値の次に出す', () => {
  const text = formatBarInfoText({ ...INFO, sessionMP: { poc: 100, val: 90, vah: 110 } }, LABELS);
  assert.equal(text.split('\n')[2], 'POC 100\tVA 90–110');
});

test('四本値が無い足（材料未着）は日時と指標だけを返す', () => {
  const text = formatBarInfoText({ ...INFO, ohlc: null }, LABELS);
  assert.deepEqual(text.split('\n'), ['2010-06-29 00:00', 'RSI\trsi 55.25']);
});

test('info が null（足の無い位置）は空文字＝呼び出し側はコピーしない', () => {
  assert.equal(formatBarInfoText(null, LABELS), '');
  assert.equal(formatBarInfoText({ time: undefined, ohlc: null, indicators: [] }), '');
});
