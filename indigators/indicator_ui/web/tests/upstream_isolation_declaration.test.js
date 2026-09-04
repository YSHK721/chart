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
//   ペイン系（panes / getPane / getHeight / paneIndex）は ISSUE-276 のペイン別凡例と
//   ペイン並べ替え（2026-08-09）で使い始めた IPaneApi 系。受け手を問わず名前だけで判定する。
const UPSTREAM_API = [
  'addSeries', 'addPane', 'removePane', 'createPriceLine', 'setData', 'applyOptions',
  'removeSeries', 'removePriceLine', 'subscribeCrosshairMove', 'createTextWatermark',
  'timeScale', 'attachPrimitive', 'priceScale',
  'panes', 'getPane', 'getHeight', 'paneIndex',
];

// 受け手（レシーバ）を見て判定する API。**名前が upstream 以外の標準 API と衝突するもの**だけを
//   ここへ置く。名前だけで判定すると誤検出になり、誤検出が続く検定は信用されず無効化される。
//
//   moveTo: upstream の `IPaneApi.moveTo(index)`（ペインの並べ替え）と、canvas 2D の
//     `CanvasRenderingContext2D.moveTo(x, y)`（パスの開始点）が同名。実測（2026-08-09）で
//     front 配下の `.moveTo(` は chart_renderer.js の `pane.moveTo(to)` 1 件と、
//     market_profile_primitive.js / pair_lines_primitive.js の `ctx.moveTo(x, y)` 4 件。
//     名前だけを見る判定に moveTo を足すと、隔離単位の外に新しい描画ファイルが増えた瞬間に
//     `ctx.moveTo` だけで落ちる（upstream とは無関係な誤検出）。
//     そこで **受け手が canvas 2D コンテキスト（末尾が ctx / context）のときだけ除外**する。
//     未知の受け手は upstream 側に倒す（フェイルクローズ）＝取りこぼしより誤検出を選ぶ。
//     除外を増やすときは、なぜその受け手が upstream でないのかをここへ書く。
const RECEIVER_QUALIFIED_API = [
  { api: 'moveTo', notUpstreamReceiver: /(?:ctx|context)$/i },
];

function frontFiles() {
  return readdirSync(FRONT)
    .filter((n) => n.endsWith('.js'))
    .filter((n) => statSync(join(FRONT, n)).isFile());
}

// 隔離単位（このグループ内でのみ upstream API を呼んでよい）の**自己申告マーカー**。
//
//   かつてここは 11 行のハードコード列挙だった。隔離単位に属する協働子を新設するたびに
//   「実体（新ファイル）」と「宣言（本ファイルの列挙）」の 2 か所を同時に直す必要があり、
//   片方だけ更新される形になっていた——これは ISSUE-262 で潰したはずの失敗型そのものである
//   （宣言と施行が別々に手書きされ、片方だけ更新される）。宣言はファイル自身が持ち、
//   本検定は走査するだけにする（宣言と実体が同じ場所にある＝ずれ得ない）。
//
//   書式: ファイル冒頭付近のコメント行に `// @upstream-isolation: <自分のファイル名>` を 1 行。
//   自分のファイル名と一致する申告だけを受理する（他ファイルの名前を騙って許可を横取りできない）。
const ISOLATION_DECLARATION_RE = /^\s*\/\/\s*@upstream-isolation:\s*(\S+)\s*$/;

// ソース集合（ファイル名 -> ソース）から自己申告の集合を導く（純関数＝検出器自身を検定できる）。
function declaredIsolationUnits(sourcesByName) {
  const names = new Set();
  for (const [name, src] of Object.entries(sourcesByName)) {
    for (const line of src.split('\n')) {
      const m = line.match(ISOLATION_DECLARATION_RE);
      if (m && m[1] === name) {
        names.add(name);
      }
    }
  }
  return names;
}

function frontSources() {
  const out = {};
  for (const name of frontFiles()) {
    out[name] = readFileSync(join(FRONT, name), 'utf8');
  }
  return out;
}

const ALLOWED = declaredIsolationUnits(frontSources());

// 1 行のコードが upstream API を呼んでいるか。名前だけで判定するものと、受け手を見るものの 2 系統。
function lineCallsUpstream(code) {
  for (const api of UPSTREAM_API) {
    if (new RegExp(`\\.${api}\\s*\\(`).test(code)) return true;
  }
  for (const { api, notUpstreamReceiver } of RECEIVER_QUALIFIED_API) {
    // 受け手＝`.api(` の直前にある識別子（`panes()[0].moveTo(` のような式は空文字＝未知）。
    const re = new RegExp(`([A-Za-z0-9_$]*)\\s*\\.\\s*${api}\\s*\\(`, 'g');
    for (const m of code.matchAll(re)) {
      if (!notUpstreamReceiver.test(m[1])) return true;   // 未知の受け手は upstream 扱い。
    }
  }
  return false;
}

function callsUpstream(source) {
  const hits = [];
  source.split('\n').forEach((line, i) => {
    const code = line.trim();
    if (code.startsWith('//') || code.startsWith('*') || code.startsWith('/*')) return;
    if (lineCallsUpstream(code)) hits.push(`${i + 1}: ${code.slice(0, 80)}`);
  });
  return hits;
}

// chart_renderer.js 冒頭が宣言している API 名（「JS API 名（… / … / …）を」の括弧内）。
function declaredApiNames() {
  const src = readFileSync(join(FRONT, 'chart_renderer.js'), 'utf8');
  const m = src.match(/JS API 名（([\s\S]*?)）を/);
  assert.ok(m, 'chart_renderer.js 冒頭の API 名宣言が見つからない（宣言の書式を変えたら本検定も直す）');
  return m[1].replace(/\/\//g, ' ').split('/').map((s) => s.trim()).filter(Boolean);
}

test('受け手つき判定は upstream の moveTo だけを拾う（canvas の同名 API と取り違えない）', () => {
  // 判定そのものを検定する。ここが緩むと、隔離の施行が静かに空振りする（ISSUE-262 の再発型）。
  assert.equal(lineCallsUpstream('pane.moveTo(to);'), true, 'upstream の並べ替えを見逃している');
  assert.equal(lineCallsUpstream('panes()[0].moveTo(2);'), true, '受け手が式のとき見逃している');
  assert.equal(lineCallsUpstream('ctx.moveTo(x1, y1);'), false, 'canvas の moveTo を誤検出している');
  assert.equal(lineCallsUpstream('this._ctx.moveTo(0, y);'), false, 'canvas の moveTo を誤検出している');
  assert.equal(lineCallsUpstream('renderingContext.moveTo(x, 0);'), false, 'canvas の moveTo を誤検出している');
  // ペイン系（名前だけで判定する側）も、実際に拾えることを固定する。
  assert.equal(lineCallsUpstream('const panes = this._chart.panes() ?? [];'), true);
  assert.equal(lineCallsUpstream('const pane = ms.getPane();'), true);
  assert.equal(lineCallsUpstream('const h = pane.getHeight();'), true);
  assert.equal(lineCallsUpstream('const idx = slot.pane.paneIndex();'), true);
  // 同名の**プロパティ参照**は呼び出しではない（凡例 DTO の g.paneIndex を誤検出しない）。
  assert.equal(lineCallsUpstream('const target = g.paneIndex;'), false);
});

test('隔離単位は自己申告マーカーから導かれる（申告を外したファイルは許可から外れる）', () => {
  // 検出器そのものの検定（ここが空振りすると、許可集合が静かに全件 or 0 件になる）。
  assert.deepEqual(
    [...declaredIsolationUnits({ 'a.js': '// @upstream-isolation: a.js\nchart.addSeries();' })],
    ['a.js'], '自己申告を拾えていない');
  assert.deepEqual(
    [...declaredIsolationUnits({ 'a.js': 'chart.addSeries();' })],
    [], '申告の無いファイルを許可している');
  assert.deepEqual(
    [...declaredIsolationUnits({ 'a.js': '// @upstream-isolation: b.js\nchart.addSeries();' })],
    [], '他ファイルの名前を騙る申告を受理している');
});

test('宣言（chart_renderer.js 冒頭）と施行（本検定の API 名）が一致する', () => {
  // ISSUE-262 の原因は「宣言と施行が別々に手書きされ、片方だけ更新される」ことだった。
  //   両者の一致をここで固定し、片側追加を落とす（宣言の形骸化を構造的に不可能にする）。
  const enforced = [...UPSTREAM_API, ...RECEIVER_QUALIFIED_API.map((e) => e.api)].sort();
  const declared = [...declaredApiNames()].sort();
  assert.deepEqual(declared, enforced,
    '隔離単位の宣言（chart_renderer.js 冒頭）と施行（本検定の API 名リスト）が食い違っています。'
    + ' 両方を同時に更新してください。');
});

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
