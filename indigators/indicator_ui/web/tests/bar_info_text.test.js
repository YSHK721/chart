// bar_info_text.js（足 1 本のコピー用テキスト整形）の仕様検証。
//
// 設計入力: ユーザー指示（2026-08-09）「情報ウィンドの価格情報や指標の値を一括コピー。
//   日付・時間・四本値・指標をクリップボードへ」＋ ユーザー指摘（2026-08-10）
//   「コピーした情報の内容が分からない（どのチャート・どのパラメータか）」。
// 規約の要点（本検定が固定するもの）:
//   - 1 行目は銘柄＋時間足（貼り付け先には画面が無い＝値だけでは別チャートと区別できない）。
//   - 表記は情報ウィンドと同じ材料・同じ並び（日時 → 四本値 → 当日 MP → 指標）。
//   - 指標行の見出しは凡例ラベル＋適用中パラメータ（同じ指標でも期間が違えば別の値）。
//   - その足に値が無い系列は出さない（空欄・0 を捏造しない）。
// 構造: Arrange-Act-Assert。純関数のため DOM・チャート非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { formatBarInfoText, indicatorHeading } from '../js/adapter/front/bar_info_text.js';

const INFO = {
  time: 1277769600,          // 2010-06-29 00:00 UTC
  ohlc: { open: 1.2, high: 1.6, low: 1.1, close: 1.5 },
  sessionMP: null,
  indicators: [
    { instanceId: 'rsi#1', values: [{ name: 'rsi', value: 55.25 }] },
  ],
};

const LABELS = new Map([['rsi#1', 'RSI']]);
const CTX = { symbol: 'NI225', timeframe: '1D', labels: LABELS };

test('銘柄・時間足 → 日時 → 四本値 → 指標の順で返す', () => {
  const text = formatBarInfoText(INFO, CTX);
  assert.deepEqual(text.split('\n'), [
    'NI225\t1D',
    '2010-06-29 00:00',
    'O 1.2\tH 1.6\tL 1.1\tC 1.5',
    'RSI\trsi 55.25',
  ]);
});

test('文脈未注入（銘柄・時間足なし）でも値は落とさない（先頭行を作らない）', () => {
  const text = formatBarInfoText(INFO, { labels: LABELS });
  assert.deepEqual(text.split('\n')[0], '2010-06-29 00:00');
});

test('片方だけの文脈は有る方だけ書く（空欄を並べない）', () => {
  assert.equal(formatBarInfoText(INFO, { symbol: 'NI225' }).split('\n')[0], 'NI225');
  assert.equal(formatBarInfoText(INFO, { timeframe: '5m' }).split('\n')[0], '5m');
});

test('指標行の見出しは凡例ラベル（未知 instanceId は instanceId 表記へ縮退）', () => {
  const text = formatBarInfoText(INFO, { ...CTX, labels: new Map() });
  assert.match(text, /^rsi#1\trsi 55\.25$/m);
});

test('labels 未注入でも落ちない（instanceId 表記）', () => {
  assert.match(formatBarInfoText(INFO, {}), /^rsi#1\t/m);
  assert.match(formatBarInfoText(INFO), /^rsi#1\t/m);
});

test('その足に値が無い系列は出さない・全系列が無い指標は行ごと出さない', () => {
  const info = {
    ...INFO,
    indicators: [
      { instanceId: 'ma#1', values: [{ name: 'ma20', value: 10 }, { name: 'ma50', value: undefined }] },
      { instanceId: 'rsi#1', values: [{ name: 'rsi', value: null }] },
    ],
  };
  const lines = formatBarInfoText(info, {
    ...CTX, labels: new Map([['ma#1', 'MA'], ['rsi#1', 'RSI']]),
  }).split('\n');
  assert.equal(lines[3], 'MA\tma20 10');
  assert.equal(lines.length, 4);   // RSI の行は作られない
});

test('当日 MP（POC/VA）は DTO に載っているときだけ四本値の次に出す', () => {
  const text = formatBarInfoText({ ...INFO, sessionMP: { poc: 100, val: 90, vah: 110 } }, CTX);
  assert.equal(text.split('\n')[3], 'POC 100\tVA 90–110');
});

test('四本値が無い足（材料未着）は日時と指標だけを返す', () => {
  const text = formatBarInfoText({ ...INFO, ohlc: null }, CTX);
  assert.deepEqual(text.split('\n'), ['NI225\t1D', '2010-06-29 00:00', 'RSI\trsi 55.25']);
});

test('info が null（足の無い位置）は空文字＝呼び出し側はコピーしない', () => {
  assert.equal(formatBarInfoText(null, CTX), '');
  assert.equal(formatBarInfoText({ time: undefined, ohlc: null, indicators: [] }), '');
});

// ---------------------------------------------------------------------------
// indicatorHeading（凡例ラベル ＋ 適用中パラメータ）
// ---------------------------------------------------------------------------

test('indicatorHeading: パラメータを括弧で添える（値の意味を定める）', () => {
  assert.equal(
    indicatorHeading({ label: 'RSI', params: { length: 14, source: 'close' } }),
    'RSI (length=14, source=close)',
  );
});

test('indicatorHeading: パラメータ無し・空値はラベルだけ', () => {
  assert.equal(indicatorHeading({ label: 'MA' }), 'MA');
  assert.equal(indicatorHeading({ label: 'MA', params: {} }), 'MA');
  assert.equal(indicatorHeading({ label: 'MA', params: { a: null, b: undefined, c: '' } }), 'MA');
});

test('indicatorHeading: 非スカラーは載せない（[object Object] を書かない）', () => {
  const h = indicatorHeading({ label: 'X', params: { w: 5, obj: { a: 1 }, arr: [1, 2] } });
  assert.equal(h, 'X (w=5)');
});

test('indicatorHeading: ラベル欠落は instanceId へ縮退・row 不在は空文字', () => {
  assert.equal(indicatorHeading({ instanceId: 'x#1', params: { n: 1 } }), 'x#1 (n=1)');
  assert.equal(indicatorHeading(null), '');
});
