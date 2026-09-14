// tf_period_tooltip.js（ホバー読取ツールチップ）の検証（fake DOM・lwc 非依存）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TfPeriodTooltip, formatTooltipLines, formatPeriodLabel } from '../js/adapter/front/tf_period_tooltip.js';

test('formatPeriodLabel: 日内周期は HH:MM（UTC）・1D（86400 の倍数）は MM-DD', () => {
  assert.equal(formatPeriodLabel(1783944420), '12:07'); // 2026-07-13 12:07 UTC
  assert.equal(formatPeriodLabel(1783900800), '07-13'); // 2026-07-13 00:00（1D 周期始端）
  assert.equal(formatPeriodLabel(null), '');
});

test('formatTooltipLines: count 列は 滞在/シェア/POC/VA/周期計・zp 列（実数）は z 表記', () => {
  const count = formatTooltipLines({
    timeLabel: '12:07', price: 67587.0105, value: 5, poc: 67590, vaLow: 67585, vaHigh: 67595, tpoUnits: 40,
  });
  assert.deepEqual(count, [
    '12:07', '価格 67587.0105', '滞在 5 tick（12.5%）', 'POC 67590', 'VA 67585〜67595', '周期計 40 tick',
  ]);
  const zp = formatTooltipLines({ timeLabel: '12:00', price: 67590, value: 2.4, poc: 67590, vaLow: null, vaHigh: null, tpoUnits: 0 });
  assert.deepEqual(zp, ['12:00', '価格 67590', 'z +2.4', 'POC 67590']);
});

// fake DOM: createElement/appendChild/clientWidth 等の最小面。
function fakeDom() {
  const el = {
    className: '', textContent: '', style: {}, offsetWidth: 100, offsetHeight: 50,
  };
  const container = { clientWidth: 800, clientHeight: 600, children: [], appendChild(e) { this.children.push(e); } };
  return { doc: { createElement: () => el }, container, el };
}

test('show/hide: カーソル近傍に表示し（右下オフセット）、hide で消える', () => {
  const { doc, container, el } = fakeDom();
  const tip = new TfPeriodTooltip({ document: doc, container });
  tip.show(100, 200, { price: 67587, value: 3, tpoUnits: 30 });
  assert.equal(el.style.display, 'block');
  assert.equal(el.style.left, '114px');
  assert.equal(el.style.top, '214px');
  assert.ok(el.textContent.includes('滞在 3 tick'));
  tip.hide();
  assert.equal(el.style.display, 'none');
});

test('show: 右端/下端でははみ出さないよう反対側へフリップする', () => {
  const { doc, container, el } = fakeDom();
  const tip = new TfPeriodTooltip({ document: doc, container });
  tip.show(780, 590, { price: 1, value: 1, tpoUnits: 1 });
  assert.equal(el.style.left, `${780 - 14 - 100}px`);
  assert.equal(el.style.top, `${590 - 14 - 50}px`);
});

test('document/container 不在は全メソッド no-op（例外を出さない）', () => {
  const tip = new TfPeriodTooltip({});
  tip.show(0, 0, { price: 1, value: 1, tpoUnits: 1 });
  tip.hide();
});
