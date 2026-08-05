// upstream（lightweight-charts）隔離の宣言を **検定で強制する**（ISSUE-262）。
//
// chart_renderer.js の冒頭は「lightweight-charts の JS API 名を呼ぶのは本ファイルだけ。
//   他ファイルでこれらの API 名を参照しない（§2.2 grep 0 件強制）」と宣言していた。
//   しかし「強制」する仕組みは存在せず、実際には 11 ファイルが upstream API を呼んでいた。
//   宣言だけが残り、lightweight-charts のバージョン更新コストは 1 ファイルに閉じていなかった。
//
// 本検定は宣言を**実態に合わせて正した上で施行**する。隔離単位は 1 ファイルではなく、
//   (a) ChartRenderer とその内部協働子、(b) チャート生成の bootstrap、(c) lwc プラグイン契約
//   （ISeriesPrimitive 実装＝chart を受け取るのが仕様）の 3 グループ。
//   これ以外のファイルが upstream API を呼んだら落とす。
//
// 隔離単位を広げたい場合は ALLOWED へ追加し、その理由をここに書く（宣言と施行を同時に更新する）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative } from 'node:path';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const FRONT = join(WEB, 'js', 'adapter', 'front');

// upstream（lightweight-charts）の API 名。chart_renderer.js の宣言に挙がっているものに、
//   実測で使われている timeScale / attachPrimitive / priceScale を加える。
const UPSTREAM_API = [
  'addSeries', 'addPane', 'removePane', 'createPriceLine', 'setData', 'applyOptions',
  'removeSeries', 'removePriceLine', 'subscribeCrosshairMove', 'createTextWatermark',
  'timeScale', 'attachPrimitive', 'priceScale',
];

// 隔離単位（このグループ内でのみ upstream API を呼んでよい）。
const ALLOWED = new Set([
  // (a) ChartRenderer 本体と、その内部協働子（host 経由で同一隔離単位に属する）。
  'chart_renderer.js', 'series_drawer.js', 'candle_feed.js', 'scale_controller.js',
  // (b) チャート生成の bootstrap（自ファイル冒頭で隔離役を宣言している）。
  'chart_bootstrap.js',
  // (c) lwc プラグイン契約（ISeriesPrimitive）の実装。chart を受け取るのが upstream 仕様。
  'market_profile_primitive.js', 'tickvol_bands_primitive.js', 'pair_lines_primitive.js',
  'trade_markers_renderer.js', 'mp_chart_layout.js',
  // (d) 合成根。可視範囲の購読のみ upstream を触る（ChartRenderer へ寄せるのが望ましいが、
  //     現状は隔離単位として明示する。狭めるときは本行を消して落ちる箇所を直す）。
  'composition_root_front.js',
]);

function frontFiles() {
  return readdirSync(FRONT)
    .filter((n) => n.endsWith('.js'))
    .filter((n) => statSync(join(FRONT, n)).isFile());
}

function callsUpstream(source) {
  const hits = [];
  source.split('\n').forEach((line, i) => {
    const code = line.trim();
    if (code.startsWith('//') || code.startsWith('*') || code.startsWith('/*')) return;
    for (const api of UPSTREAM_API) {
      if (new RegExp(`\\.${api}\\s*\\(`).test(code)) hits.push(`${i + 1}: ${code.slice(0, 80)}`);
    }
  });
  return hits;
}

test('upstream(lightweight-charts) API を呼ぶのは宣言した隔離単位だけ', () => {
  const offenders = [];
  for (const name of frontFiles()) {
    if (ALLOWED.has(name)) continue;
    const hits = callsUpstream(readFileSync(join(FRONT, name), 'utf8'));
    if (hits.length) offenders.push(`${name}\n    ${hits.join('\n    ')}`);
  }
  assert.deepEqual(offenders, [],
    `隔離単位の外で upstream API を呼んでいます:\n  ${offenders.join('\n  ')}\n`
    + '  ChartRenderer 経由へ寄せるか、隔離単位を広げる理由を本テストへ書いてください。');
});

test('隔離単位の許可リストに、実際には upstream を呼ばないファイルが残っていない', () => {
  // 許可を過剰に広げたまま放置すると、宣言が再び形骸化する（今回の再発源）。
  const stale = [...ALLOWED].filter((name) => {
    try {
      return callsUpstream(readFileSync(join(FRONT, name), 'utf8')).length === 0;
    } catch { return false; }   // 存在しない名前は次のテストで落とす
  });
  assert.deepEqual(stale, [],
    `許可リストに不要なエントリが残っています: ${stale.join(', ')}。隔離単位を狭めてください。`);
});

test('許可リストのファイルはすべて実在する（リネーム時に穴を残さない）', () => {
  const missing = [...ALLOWED].filter((name) => {
    try { statSync(join(FRONT, name)); return false; } catch { return true; }
  });
  assert.deepEqual(missing, [], `許可リストに実在しないファイルがあります: ${missing.join(', ')}`);
});

test('隔離単位の外から chart インスタンスを直接受け取っていない（間接的な迂回の検出）', () => {
  // upstream API を呼ばずとも chart を握れば将来の迂回口になる。primitive は upstream 仕様上
  //   chart を受け取るため除外する。
  const primitives = new Set([
    'market_profile_primitive.js', 'tickvol_bands_primitive.js', 'pair_lines_primitive.js',
    'trade_markers_renderer.js', 'mp_chart_layout.js', 'chart_bootstrap.js',
    'chart_renderer.js', 'series_drawer.js', 'candle_feed.js', 'scale_controller.js',
    'composition_root_front.js',
  ]);
  const offenders = [];
  for (const name of frontFiles()) {
    if (primitives.has(name)) continue;
    const src = readFileSync(join(FRONT, name), 'utf8');
    src.split('\n').forEach((line, i) => {
      const code = line.trim();
      if (code.startsWith('//')) return;
      if (/^import .*\bIChartApi\b/.test(code)) offenders.push(`${name}:${i + 1}`);
    });
  }
  assert.deepEqual(offenders, [], `chart 型を直接 import しています: ${offenders.join(', ')}`);
});
