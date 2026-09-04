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
// 隔離単位の広げ方（陳腐化していた記述の是正・JS レビュー 🔵-4）:
//   許可は**ファイル自身の自己申告**である（`// @upstream-isolation: <自分のファイル名>` を 1 行）。
//   本ファイルに「許可リスト」は無い——ALLOWED は申告を走査した導出集合である。
//   広げるときは (1) 対象ファイルへ申告行を足し、(2) その理由を本ファイルの
//   EXPECTED_ISOLATION_UNITS（台帳）へ分類とともに書き足す。台帳との双方向一致を検定しているため、
//   (1) だけでは通らない＝無審査の拡大ができない（ratchet）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

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

// ソース集合から「申告はあるが名前が自分と違う」行を導く（純関数＝検出器自身を検定できる）。
//   declaredIsolationUnits が黙って捨てる行を、ここでは理由付きで可視化する。
function mismatchedDeclarations(sourcesByName) {
  const out = [];
  for (const [name, src] of Object.entries(sourcesByName)) {
    for (const line of src.split('\n')) {
      const m = line.match(ISOLATION_DECLARATION_RE);
      if (m && m[1] !== name) {
        out.push(`${name}: 申告=${m[1]}`);
      }
    }
  }
  return out.sort();
}

// chart 型（IChartApi）を直接 import している「隔離単位の外」のファイルを導く（純関数）。
//   allowed は隔離単位の集合そのものを受け取る（第 2 の列挙を作らないための引数化）。
function chartTypeImportOffenders(sourcesByName, allowed) {
  const out = [];
  for (const [name, src] of Object.entries(sourcesByName)) {
    if (allowed.has(name)) {
      continue;
    }
    src.split('\n').forEach((line, i) => {
      const code = line.trim();
      if (code.startsWith('//')) {
        return;
      }
      if (/^import .*\bIChartApi\b/.test(code)) {
        out.push(`${name}:${i + 1}`);
      }
    });
  }
  return out.sort();
}

// front の全ソースを **1 ファイル 1 回だけ** 読む単一の読取点（計算量テストがこの発行を数える）。
//   読取は注入可能（発行回数を外から観測するため）。走査対象も差し替え可能（2 点でオーダーを測る）。
function frontSources(read = (p) => readFileSync(p, 'utf8'), files = frontFiles()) {
  const out = {};
  for (const name of files) {
    out[name] = read(join(FRONT, name));
  }
  return out;
}

// 全検定が共有する唯一の読取結果。項目ごとに front を読み直さない（ISSUE-450 型の浪費を作らない）。
const SOURCES = frontSources();

const ALLOWED = declaredIsolationUnits(SOURCES);

// 隔離単位の**台帳**（JS レビュー 🟡-2 の ratchet）。
//
//   自己申告への移行で「宣言と実体がずれる」失敗型は消えたが、同時に「隔離単位を広げるときに
//   立ち止まる摩擦」も消えた——新しいファイルへ 1 行足すだけで、誰にも見えないまま許可が増える。
//   ここに現在の単位を書き出し、導出集合との**双方向一致**を検定する。拡大にはテスト編集が要る。
//
//   3 グループの内訳（本ファイル冒頭の宣言と対応する）:
//     (a) ChartRenderer とその内部協働子（描画の実装本体・状態の所有者）
//     (b) チャート生成の bootstrap／レイアウト（upstream の生成 API を呼ぶのが仕事）
//     (c) lwc プラグイン契約（ISeriesPrimitive 実装＝chart を受け取るのが upstream 仕様）
const EXPECTED_ISOLATION_UNITS = Object.freeze([
  // (a) ChartRenderer とその内部協働子
  'chart_renderer.js',
  'candle_feed.js',
  'series_drawer.js',
  'scale_controller.js',
  'pane_geometry_controller.js',
  'chrome_color_controller.js',
  'crosshair_readout_builder.js',
  // (b) チャート生成の bootstrap／レイアウト／合成根
  'chart_bootstrap.js',
  'mp_chart_layout.js',
  'composition_root_front.js',
  // (c) lwc プラグイン契約（primitive 実装・描画器）
  'market_profile_primitive.js',
  'tickvol_bands_primitive.js',
  'pair_lines_primitive.js',
  'trade_markers_renderer.js',
]);

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

// 隔離単位の**外**で upstream API を呼んでいるファイルを導く（純関数）。
function upstreamCallOffenders(sourcesByName, allowed) {
  const out = [];
  for (const [name, src] of Object.entries(sourcesByName)) {
    if (allowed.has(name)) {
      continue;
    }
    const hits = callsUpstream(src);
    if (hits.length) {
      out.push(`${name}\n    ${hits.join('\n    ')}`);
    }
  }
  return out.sort();
}

// 隔離単位を申告しているのに upstream API を 1 つも呼ばないファイルを導く（純関数）。
function staleIsolationUnits(sourcesByName, allowed) {
  return [...allowed]
    .filter((name) => (name in sourcesByName) && callsUpstream(sourcesByName[name]).length === 0)
    .sort();
}

// chart_renderer.js 冒頭が宣言している API 名（「JS API 名（… / … / …）を」の括弧内）。
function declaredApiNames() {
  const src = SOURCES['chart_renderer.js'];
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

test('隔離単位の台帳（EXPECTED_ISOLATION_UNITS）と実際の申告集合が双方向に一致する', () => {
  // なぜ在るか（JS レビュー 🟡-2）: 許可を**自己申告**へ移した結果、隔離単位を広げるのに
  //   テストを 1 文字も触らなくてよくなった。宣言の二重管理（ISSUE-262 の失敗型）は消えた
  //   代わりに、「広げるときに必ず立ち止まる摩擦」まで一緒に消えている。ここへ台帳を置き、
  //   拡大に**テスト編集を要する**状態（ratchet）を復元する。
  //   双方向で測る: 台帳にあって申告が無い（＝申告の外し忘れ）／申告があって台帳に無い
  //   （＝無審査の拡大）のどちらも落とす。
  assert.deepEqual([...ALLOWED].sort(), [...EXPECTED_ISOLATION_UNITS].sort(),
    '隔離単位が台帳と食い違っています。単位を広げる/狭めるときは、その理由を本ファイルへ'
    + '書いた上で EXPECTED_ISOLATION_UNITS も更新してください。');
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
  const offenders = upstreamCallOffenders(SOURCES, ALLOWED);
  assert.deepEqual(offenders, [],
    `隔離単位の外で upstream API を呼んでいます:\n  ${offenders.join('\n  ')}\n`
    + '  ChartRenderer 経由へ寄せるか、隔離単位を広げる理由を本テストへ書いてください。');
});

test('隔離単位の許可リストに、実際には upstream を呼ばないファイルが残っていない', () => {
  // 許可を過剰に広げたまま放置すると、宣言が再び形骸化する（今回の再発源）。
  const stale = staleIsolationUnits(SOURCES, ALLOWED);
  assert.deepEqual(stale, [],
    `許可リストに不要なエントリが残っています: ${stale.join(', ')}。隔離単位を狭めてください。`);
});

// 旧「許可リストのファイルはすべて実在する」検定は **恒真**だったため置換した（JS レビュー 🟡-2）。
//   理由: ALLOWED は frontFiles()（実在ファイル名）を走査し、`申告名 === 自ファイル名` の行だけを
//   採る導出集合である。実在しない名前は構造上 1 つも入り得ず、missing は常に空だった。
//   ハードコード列挙の時代（実在しない名前が残り得た）の検定が、導出化のあとも残っていた。
//
// 代わりに置くのは、導出方式で**実際に起こり得る**穴である: 申告名が自ファイル名と食い違う行
//   （リネームの直し漏れ・複製時の書き換え忘れ）。この行は静かに無視されるため、書いた本人は
//   「隔離単位に入れた」と思い込むのに許可は付いていない。upstream API を呼んだ瞬間に別検定が
//   落ちるが、原因（申告名の食い違い）はメッセージに出ない。ここで名指しで落とす。

test('申告名が自ファイル名と食い違う宣言が無い（静かに無視される申告を残さない）', () => {
  const mismatched = mismatchedDeclarations(SOURCES);
  assert.deepEqual(mismatched, [],
    `申告名がファイル名と食い違っています（この申告は無視されます）: ${mismatched.join(', ')}`);
});

test('食い違い検出器そのものの検定（空振りしていない）', () => {
  assert.deepEqual(
    mismatchedDeclarations({ 'a.js': '// @upstream-isolation: b.js\n' }),
    ['a.js: 申告=b.js'], 'リネーム漏れの申告を見逃している');
  assert.deepEqual(
    mismatchedDeclarations({ 'a.js': '// @upstream-isolation: a.js\n' }),
    [], '正しい申告を食い違いと誤判定している');
  assert.deepEqual(
    mismatchedDeclarations({ 'a.js': 'export const a = 1;\n' }),
    [], '申告の無いファイルを食い違いと誤判定している');
});

test('隔離単位の外から chart インスタンスを直接受け取っていない（間接的な迂回の検出）', () => {
  // upstream API を呼ばずとも chart を握れば将来の迂回口になる。「握ってよいのは誰か」は
  //   隔離単位そのもの（ALLOWED）であり、**第 2 の列挙を持たない**（JS レビュー 🔵-4）。
  //   かつてここには 11 件のハードコード集合があり、隔離単位 14 件とずれていた——協働子を
  //   抽出して単位へ入れても、この集合に足し忘れれば「単位の中なのに違反」と誤って落ちる。
  const offenders = chartTypeImportOffenders(SOURCES, ALLOWED);
  assert.deepEqual(offenders, [], `chart 型を直接 import しています: ${offenders.join(', ')}`);
});

test('chart 型 import 検出器そのものの検定（空振りせず、単位の中を誤検出しない）', () => {
  const line = "import { IChartApi } from 'lightweight-charts';\n";
  assert.deepEqual(
    chartTypeImportOffenders({ 'outsider.js': line }, new Set()),
    ['outsider.js:1'], '隔離単位の外の chart 型 import を見逃している');
  assert.deepEqual(
    chartTypeImportOffenders({ 'unit.js': line }, new Set(['unit.js'])),
    [], '隔離単位の中を誤検出している');
  assert.deepEqual(
    chartTypeImportOffenders({ 'doc.js': `// ${line}` }, new Set()),
    [], 'コメント内の記述を誤検出している');
  // 現に隔離単位である協働子（旧ハードコード集合には無かった）が除外側に入ること。
  assert.ok(ALLOWED.has('pane_geometry_controller.js'),
    '測定の前提（当該協働子が隔離単位である）が崩れている');
  assert.deepEqual(
    chartTypeImportOffenders({ 'pane_geometry_controller.js': line }, ALLOWED),
    [], '隔離単位の協働子を chart 型 import の違反として落としている');
});

// --------------------------------------------------------------------------- //
// 計算量: 検定 1 巡の front 読取 − 対象ファイル数 = 0（項目ごとに読み直さない）
// --------------------------------------------------------------------------- //
// なぜ在るか（絶対命令 2026-08-28）: 出力（offender 一覧）が正しいままでも、項目ごとに front を
//   丸ごと読み直す実装は「作ってから捨てる」浪費であり、状態検証では原理的に落ちない。
//   発行回数そのものは期待値に焼き込まず、**無駄の不在**（発行 − 使用 = 0）だけを固定する。

function countingRead(reads) {
  return (p) => { reads.push(p); return readFileSync(p, 'utf8'); };
}

for (const fileCount of [5, 12]) {
  test(`計算量: 検定 1 巡の読取 − 対象ファイル数 = 0（対象 ${fileCount} 本）`, () => {
    const files = frontFiles().slice(0, fileCount);
    const reads = [];
    const sources = frontSources(countingRead(reads), files);

    // 全項目を**同じ集合**の上で走らせる（項目ごとに読み直さない）。
    const allowed = declaredIsolationUnits(sources);
    mismatchedDeclarations(sources);
    chartTypeImportOffenders(sources, allowed);
    upstreamCallOffenders(sources, allowed);
    staleIsolationUnits(sources, allowed);

    assert.equal(reads.length - Object.keys(sources).length, 0,
      `同じファイルを読み直している: reads=${reads.length} files=${Object.keys(sources).length}`);
    assert.equal(Object.keys(sources).length, fileCount);
  });
}

for (const itemCount of [2, 5]) {
  test(`計算量: 検査項目を ${itemCount} 件に増やしても読取は増えない（オーダーの表明）`, () => {
    const files = frontFiles().slice(0, 12);
    const reads = [];
    const sources = frontSources(countingRead(reads), files);
    const allowed = declaredIsolationUnits(sources);
    for (let i = 0; i < itemCount; i += 1) {
      mismatchedDeclarations(sources);
      chartTypeImportOffenders(sources, allowed);
      upstreamCallOffenders(sources, allowed);
    }
    assert.equal(reads.length - Object.keys(sources).length, 0);
  });
}

test('計算量ゲートの検出力: 項目ごとに読み直す変異で赤になる', () => {
  const files = frontFiles().slice(0, 12);
  const reads = [];
  const read = countingRead(reads);
  const first = frontSources(read, files);
  frontSources(read, files);          // 捨てられる読取（項目ごとの読み直しの再現）。
  assert.notEqual(reads.length - Object.keys(first).length, 0,
    '変異を検出できていない（検査が空振り）');
});
