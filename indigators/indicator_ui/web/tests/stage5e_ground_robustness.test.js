// stage5e_ground_robustness.test.js — 段階 5-E の新規配線点が地を変えても壊れないこと（通過条件 6）。
//
// 5-D で実測された病因: 加法 delta は明るい地で飽和・反転する（52 slot 中 14 が飽和）。地に
//   従属する系（面・文字・構造線）は ramp / crTarget で地に相対化することで構成的に解消した。
//
// 本段階が足した配線点の内訳（実測）:
//   有彩色の濃淡  … tradeProfit / tradeLoss / tradeSideBuy / tradeSideSell / pairLineWin /
//                    pairLineLoss / tickvolBand / levelSchemeCalm / levelSchemeHot
//                    → 通過条件 6 の但し書き「有彩色の濃淡は地に相対化しなくてよい」に該当。
//                       これらは「そのトークン自身の色」であって地の関数ではない。
//   地に従属する系 … 無し（枠・文字は 5-D の既存配線点 uiPanel / uiText / uiBorder を再利用した）
//   text 系       … watermark（text の高不透明度）→ 地に従属する系なので本テストの対象。
//
// よって本テストが確かめるのは「有彩色が地によらず飽和しないこと」と「text 系が地から
//   区別できること」である。

import test from 'node:test';
import assert from 'node:assert/strict';

import { CHROME_SLOTS } from '../js/usecase/chrome_tokens.js';
import { resolveChromeSlotColor, resolveAllChrome } from '../js/usecase/color_resolver.js';
import { contrastRatio, toChannels } from '../js/domain/color_value.js';

// 5-D と同じ 6 地（暗い既定・濃紺・純白・明るい紙・中間灰・純黒）。
const GROUNDS = ['#131722', '#0d1b3e', '#ffffff', '#f5f5f5', '#808080', '#000000'];

// 段階 5-E で足した配線点（チャート上の描画物）。
const STAGE_5E_IDS = [
  'tradeProfit', 'tradeLoss', 'tradeSideBuy', 'tradeSideSell',
  'pairLineWin', 'pairLineLoss', 'tickvolBand', 'watermark',
  'levelSchemeCalm', 'levelSchemeHot',
];

// rgba(...) / #rrggbb のどちらでも RGB を取り出す。
function channelsOf(color) {
  const m = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/.exec(String(color));
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : toChannels(color);
}

// 有彩色テーマ（地だけを差し替え、図の色は据え置く）。
function themeOn(ground) {
  return {
    roleColors: {
      surface: ground,
      profit: '#26a69a', loss: '#ef5350',
      bullish: '#00bfa5', bearish: '#ff5252',
      accent: '#2962ff', alert: '#e0a24a', neutral: '#2e7d32', text: '#d1d4dc',
    },
  };
}

test('通過条件 6: 段階 5-E の配線点は 6 地すべてで解決でき、null にならない', () => {
  // Arrange / Act / Assert
  for (const ground of GROUNDS) {
    for (const id of STAGE_5E_IDS) {
      const color = resolveChromeSlotColor({ slotId: id, theme: themeOn(ground) });
      assert.ok(color != null && color !== '', `${id} @ ${ground}: 解決できない`);
    }
  }
});

test('通過条件 6: 有彩色の配線点が 6 地で飽和しない（全チャネル 0 / 255 に張り付かない）', () => {
  // 飽和＝情報が失われた状態。加法 delta を持つ配線点（levelSchemeHot）が明るい地でも
  //   潰れないことをここで押さえる。有彩色は地の関数ではないので、地を変えても値は動かない
  //   ことが期待される（動いてしまうなら、地に従属させるべきでないものを従属させている）。
  const chromatic = STAGE_5E_IDS.filter((id) => id !== 'watermark');
  for (const ground of GROUNDS) {
    for (const id of chromatic) {
      const ch = channelsOf(resolveChromeSlotColor({ slotId: id, theme: themeOn(ground) }));
      const allLow = ch.every((c) => c === 0);
      const allHigh = ch.every((c) => c === 255);
      assert.equal(allLow || allHigh, false, `${id} @ ${ground}: 飽和した（${ch.join(',')}）`);
    }
  }
});

test('通過条件 6: 有彩色の配線点は地を変えても値が動かない（地の関数になっていない）', () => {
  // 有彩色は「そのトークン自身の色」である。地に相対化すると、背景を変えただけで利益色が
  //   変わるという意味の壊れ方をする。動かないことを構成として固定する。
  const chromatic = STAGE_5E_IDS.filter((id) => id !== 'watermark');
  for (const id of chromatic) {
    const values = GROUNDS.map((g) => resolveChromeSlotColor({ slotId: id, theme: themeOn(g) }));
    assert.equal(new Set(values).size, 1, `${id}: 地によって値が変わる（${[...new Set(values)].join(' / ')}）`);
  }
});

test('通過条件 6: 成果色と方向色は 6 地すべてで互いに区別できる（分離が地に依存しない）', () => {
  // 意味を分けた以上、テーマが別の色を宣言したときに実際に別の色として出ることを確かめる。
  for (const ground of GROUNDS) {
    const theme = { roleColors: { surface: ground, profit: '#00e676', loss: '#ff1744', bullish: '#2979ff', bearish: '#ff9100' } };
    const profit = resolveChromeSlotColor({ slotId: 'tradeProfit', theme });
    const buy = resolveChromeSlotColor({ slotId: 'tradeSideBuy', theme });
    const loss = resolveChromeSlotColor({ slotId: 'tradeLoss', theme });
    const sell = resolveChromeSlotColor({ slotId: 'tradeSideSell', theme });
    assert.notEqual(profit, buy, `@${ground}: 利益と買いが同色（分離が効いていない）`);
    assert.notEqual(loss, sell, `@${ground}: 損失と売りが同色（分離が効いていない）`);
    assert.equal(profit, '#00e676');
    assert.equal(buy, '#2979ff');
  }
});

test('通過条件 6: text 系（watermark）は 6 地すべてで地から区別できる', () => {
  // 地に従属する系はコントラストで確かめる。ウォーターマークは薄く出す意匠なので前景文字の
  //   基準（4.5）ではなく、構造線と同じく「地と同一でない」ことを条件にする。
  for (const ground of GROUNDS) {
    const color = resolveChromeSlotColor({ slotId: 'watermark', theme: themeOn(ground) });
    const [r, g, b] = channelsOf(color);
    const hex = `#${[r, g, b].map((c) => c.toString(16).padStart(2, '0')).join('')}`;
    const cr = contrastRatio(hex, ground);
    assert.ok(cr > 1.05, `watermark @ ${ground}: 地と区別できない（CR ${cr.toFixed(4)}）`);
  }
});

test('通過条件 6: 6 地のいずれでも全配線点が解決でき、台帳と同じ濃度の袋が出る', () => {
  // 配信の袋が地によって欠けないこと（欠けると、その配線点だけ前回の色が残る）。
  for (const ground of GROUNDS) {
    const { slots } = resolveAllChrome(themeOn(ground));
    assert.equal(Object.keys(slots).length, CHROME_SLOTS.length, `@${ground}: 袋の濃度が台帳と違う`);
    for (const s of CHROME_SLOTS) {
      assert.ok(slots[s.id] != null, `${s.id} @ ${ground}: 値が無い`);
    }
  }
});
