// datasetRef を URL クエリで上書きする（ISSUE-447 段階 1・A-3 案 U1）。
//
// 設計入力（唯一の仕様源）: `.doc/MT5_REALTIME_TICK_SUPPLY_BASIC_DESIGN.md` §9 承認表
//   A-3（**承認（U1）**・2026-09-01 依頼者裁定）:
//     「front の ref 選択（UI 変更）。案 U1 で承認: `datasetRef` を URL クエリで上書き
//       （**既定挙動不変**）。案 U2（セレクタ UI）は不採用」
//   §7 H9: front 表示の実 UI 確認は A-1〜A-3 承認後。
//
// 除去する原因: 入口（index.html）が対象 datasetRef を**値として自称**していたこと。
//   jp225_mt5 の実 UI 確認（H9）のたびに index.html を書き換える義務が生まれ、
//   書き換えたまま戻し忘れれば既定表示が静かに別データへ移る。値を外から与えられる
//   ようにして、入口が持つのは「既定」だけにする。
//
// 参照実装（挙動の正解を定義する既存実装・推測で書かない）:
//   `simulator/sim_ui/web/js/adapter/front/report_source_client.js:27-35` の `readJobId(search)`。
//   規約は 4 点: (1) search 文字列を注入で受ける（location を関数内で読まない＝純粋・可搬）、
//   (2) 先頭 '?' の有無を吸収、(3) 未指定は null 相当を返し**自動選択しない**、
//   (4) trim 後に空なら未指定とみなす。本モジュールは (3) の「null」を「呼び出し側が渡した
//   既定値」に置き換えただけの同型である。
//
// 検証しないこと（意図的・案 U1 の範囲外）:
//   ref の実在検証は front で行わない。未知 ref は素通しし、既存のサーバ側エラー経路
//   （`usecase/serve_candles.py` → `validation` → HTTP 400「未知の datasetRef です」）に委ねる。
//   front に第 2 の台帳を持たせると、台帳が 2 つになって静かにずれる（ISSUE-368 原因 α と同型）。
//
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  resolveDatasetRef, DATASET_REF_QUERY_PARAM,
} from '../js/adapter/front/dataset_ref_query.js';

// 入口が持つ既定（index.html と同じ値）。本ファイルはこの値を「入口から読む」検定を別に持つ。
const DEFAULT_REF = 'jp225_tick';
const MT5_REF = 'jp225_mt5';

// ---------------------------------------------------------------------------
// 既定挙動不変（案 U1 の承認条件そのもの）
// ---------------------------------------------------------------------------
test('クエリが無いとき既定 ref をそのまま返す（既定挙動不変）', () => {
  // Arrange
  const search = '';

  // Act
  const ref = resolveDatasetRef(search, DEFAULT_REF);

  // Assert
  assert.equal(ref, DEFAULT_REF);
});

test('search が未定義・null・非文字列でも既定 ref を返す（入口が壊れない）', () => {
  // Arrange / Act / Assert（境界: 型の異常値）
  for (const bad of [undefined, null, 0, 42, {}, [], true]) {
    assert.equal(resolveDatasetRef(bad, DEFAULT_REF), DEFAULT_REF);
  }
});

test('他のクエリだけがあるとき既定 ref を返す（無関係な param に反応しない）', () => {
  // Arrange
  const search = '?foo=bar&job=123&timeframe=5m';

  // Act / Assert
  assert.equal(resolveDatasetRef(search, DEFAULT_REF), DEFAULT_REF);
});

// ---------------------------------------------------------------------------
// 上書き（正常系）
// ---------------------------------------------------------------------------
test('?dataset=jp225_mt5 で ref を上書きする', () => {
  // Arrange
  const search = `?${DATASET_REF_QUERY_PARAM}=${MT5_REF}`;

  // Act
  const ref = resolveDatasetRef(search, DEFAULT_REF);

  // Assert
  assert.equal(ref, MT5_REF);
});

// 注記（変異検定の実測 2026-09-01）: 実装から `startsWith('?') ? slice(1)` を外しても本件を
//   含む 16 件は全緑である。`URLSearchParams` が先頭 '?' を自身で除くため（WHATWG URL 仕様・
//   node v24 で実測）、当該分岐は**等価変異体**＝検定の弱さではなく振る舞い上の冗長である。
//   本検定は「? 付き・? 無しのどちらでも同じ ref を得る」という契約を固定する意味で残す。
test('先頭の ? が無い search でも上書きできる（参照実装 readJobId と同じ吸収）', () => {
  // Arrange
  const search = `${DATASET_REF_QUERY_PARAM}=${MT5_REF}`;

  // Act / Assert
  assert.equal(resolveDatasetRef(search, DEFAULT_REF), MT5_REF);
});

test('他のクエリと併存していても dataset だけを読む', () => {
  // Arrange
  const search = `?a=1&${DATASET_REF_QUERY_PARAM}=${MT5_REF}&b=2`;

  // Act / Assert
  assert.equal(resolveDatasetRef(search, DEFAULT_REF), MT5_REF);
});

test('前後の空白は落として読む（コピー貼り付けの事故を通さない）', () => {
  // Arrange（%20 = 空白）
  const search = `?${DATASET_REF_QUERY_PARAM}=%20${MT5_REF}%20`;

  // Act / Assert
  assert.equal(resolveDatasetRef(search, DEFAULT_REF), MT5_REF);
});

// ---------------------------------------------------------------------------
// 不正値（境界）: 空は「未指定」として既定へ倒す
// ---------------------------------------------------------------------------
test('?dataset= （値が空）は未指定として既定 ref を返す', () => {
  // Arrange / Act / Assert
  assert.equal(
    resolveDatasetRef(`?${DATASET_REF_QUERY_PARAM}=`, DEFAULT_REF), DEFAULT_REF,
  );
});

test('?dataset=（空白のみ）は未指定として既定 ref を返す', () => {
  // Arrange / Act / Assert（空 ref を送ると /candles が無意味な 400 を返すだけで、
  //   利用者には「既定が壊れた」ようにしか見えない。未指定と同じに倒す。）
  assert.equal(
    resolveDatasetRef(`?${DATASET_REF_QUERY_PARAM}=%20%20`, DEFAULT_REF), DEFAULT_REF,
  );
});

test('値の付かない ?dataset は未指定として既定 ref を返す', () => {
  // Arrange / Act / Assert
  assert.equal(
    resolveDatasetRef(`?${DATASET_REF_QUERY_PARAM}`, DEFAULT_REF), DEFAULT_REF,
  );
});

// ---------------------------------------------------------------------------
// 未知 ref は front で弾かない（既存のサーバ側エラー経路へ委ねる）
// ---------------------------------------------------------------------------
test('台帳に無い ref も素通しする（検証はサーバの既存経路が行う）', () => {
  // Arrange
  const search = `?${DATASET_REF_QUERY_PARAM}=no_such_ref`;

  // Act
  const ref = resolveDatasetRef(search, DEFAULT_REF);

  // Assert（front に第 2 の台帳を作らない。サーバが 400「未知の datasetRef です」を返す）
  assert.equal(ref, 'no_such_ref');
});

// ---------------------------------------------------------------------------
// 入口（index.html）の結線: 既定が 1 バイトも変わっていないこと
// ---------------------------------------------------------------------------
const INDEX_HTML = readFileSync(
  fileURLToPath(new URL('../index.html', import.meta.url)), 'utf8',
);

test('入口は resolveDatasetRef を経由して datasetRef を決める', () => {
  // Assert（結線されていなければクエリ上書きは実 UI で死ぬ＝ISSUE-291 と同型の無言の死）
  assert.match(INDEX_HTML, /import \{[^}]*resolveDatasetRef[^}]*\}/);
  assert.match(INDEX_HTML, /datasetRef:\s*resolveDatasetRef\(/);
});

test('入口が渡す既定 ref は jp225_tick のままである（既定表示が動かない）', () => {
  // Arrange: 入口が resolveDatasetRef へ渡している第 2 引数（既定）を実ファイルから読む。
  const m = INDEX_HTML.match(/datasetRef:\s*resolveDatasetRef\(\s*[^,]+,\s*'([^']+)'\s*\)/);

  // Assert
  assert.ok(m, 'index.html が resolveDatasetRef(search, 既定) の形で呼んでいない');
  assert.equal(m[1], DEFAULT_REF);
  // 既定（クエリ無し）で解決した結果が従来のハードコード値と一致する。
  assert.equal(resolveDatasetRef('', m[1]), 'jp225_tick');
});

// ---------------------------------------------------------------------------
// 計算量（Test Spy・発行 − 使用 = 0）
//
// なぜ必要か（CLAUDE.md 絶対命令・ISSUE-450 同型）: 返り値が正しくても「search を
// param ごとに parse し直す」実装は状態検証では**原理的に落ちない**。本関数は入口の
// 起動経路（bootstrap 直前）に居るため、無駄な parse はそのまま初期表示を遅らせる。
// 固定するのは**無駄の不在**であり、呼び出し回数という実装詳細ではない。
// ---------------------------------------------------------------------------
function countParserConstructions(search, fallback) {
  const original = globalThis.URLSearchParams;
  let constructed = 0;
  class Counting extends original {
    constructor(...args) { super(...args); constructed += 1; }
  }
  globalThis.URLSearchParams = Counting;
  try {
    return { value: resolveDatasetRef(search, fallback), constructed };
  } finally {
    globalThis.URLSearchParams = original;
  }
}

test('search の解析は 1 回だけ（作って捨てる parse が無い）', () => {
  // Arrange / Act
  const r = countParserConstructions(
    `?a=1&${DATASET_REF_QUERY_PARAM}=${MT5_REF}&b=2`, DEFAULT_REF,
  );

  // Assert（発行した parse − 出力に使った parse = 0）
  assert.equal(r.value, MT5_REF);
  assert.equal(r.constructed, 1);
});

test('解析回数はクエリ param 数を増やしても増えない（オーダーの表明・2 点）', () => {
  // Arrange: 点 1 = param 1 件、点 2 = param 200 件。
  const few = `?${DATASET_REF_QUERY_PARAM}=${MT5_REF}`;
  const many = `?${Array.from({ length: 200 }, (_, i) => `p${i}=${i}`).join('&')}`
    + `&${DATASET_REF_QUERY_PARAM}=${MT5_REF}`;

  // Act
  const a = countParserConstructions(few, DEFAULT_REF);
  const b = countParserConstructions(many, DEFAULT_REF);

  // Assert（出力は同じ。発行は param 数に依存しない）
  assert.equal(a.value, MT5_REF);
  assert.equal(b.value, MT5_REF);
  assert.equal(a.constructed, b.constructed);
});

test('未指定の早期復帰は解析を 1 回も発行しない（既定経路に無駄を足さない）', () => {
  // Arrange / Act: 既定経路（クエリ無し）は最も頻繁に通る道である。
  const r = countParserConstructions('', DEFAULT_REF);

  // Assert
  assert.equal(r.value, DEFAULT_REF);
  assert.equal(r.constructed, 0);
});
