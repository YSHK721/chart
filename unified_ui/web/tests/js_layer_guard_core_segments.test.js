// js_layer_guard_core_segments.test.js — 層検査の「見る core の集合」がモード表に従うことを固定する
//   （SOLID 精査 ISSUE-502 段階 2・D-11）。
//
// 何が壊れていたか（実測 2026-09-06）:
//   `tools/js_layer_guard.mjs` は core 名を `['live','replay','sim','dashboard']` と**列挙**して
//   持っていた。同じ集合の唯一源は `unified_ui/web/js/mode_table.js` の `MODES` であり、両者を
//   突き合わせる検査は 1 つも無かった。したがってモード表へ 5 行目を足しても層検査は 4 core の
//   ままで、**新 core は違反しても永久に検出されない**（無音の無検査化）。列挙が新規を検出
//   できないのは js_layer_guard.mjs 自身が冒頭で禁じている形そのものである。
//
// 本ファイルが固定する不変条件（列挙の写しを作らずに固定する）:
//   1. 表の各行が、実際に検査面へ現れる（越境 URL は捕捉し、公開面は捕捉しない）。表駆動なので
//      5 つ目のモードを足せば、このテストの対象も**何も書き換えずに**増える。
//   2. 表に無い core 名は検査面に現れない（導出が広すぎない＝誤検出の器にならない）。
//   3. 表へ 1 行足すと検査面が伸びる（導出経路が生きていることの実証。列挙時代はここが死んでいた）。
//   4. 形の壊れた prefix は黙って読み替えず throw する（無音の欠落を作らない）。
//   5. 計算量: 走査の入力を増やしても正規表現の再発行は 0（絶対命令 §計算量テスト）。
//
// 置き場所: モード表の所有者（unified_ui）側に置く。検査器（tools）側に置くと、表を触る人の
//   視界に入らない。

import { describe, test, expect } from 'vitest';

import { MODES, MODE_IDS, MODE_PREFIXES } from '../js/mode_table.js';
import {
  CORE_SEGMENTS,
  buildCrossCoreModuleUrlRe,
  buildPublicUrlRe,
  coreSegmentOf,
  coreSegmentsFromPrefixes,
  crossCoreModuleUrlOffenders,
  CROSS_CORE_MODULE_URL_RE,
} from '../../../tools/js_layer_guard.mjs';

/** 表に載っていないことが保証された名前（載った日は下の前提テストが赤になる）。 */
const ABSENT_CORE = 'zz_absent_core';

/** `sources` は「絶対パス → 本文」の Map なので、fs を使わず合成できる。 */
const sourcesOf = (entries) => new Map(entries.map(([name, body]) => [`/repo/js/${name}`, body]));

describe('層検査が見る core 集合 — モード表からの導出', () => {
  test('前提: 合成に使う名前が表に無い（テストが自分で空振りしない）', () => {
    expect(MODE_IDS).not.toContain(ABSENT_CORE);
    expect(MODES.length).toBeGreaterThan(1);
  });

  // --- 1. 表の各行が実際に検査面へ現れる（表駆動＝行が増えれば対象も増える）------------- //
  test.each(MODES.map((m) => [m.id, m.prefix]))(
    'モード表の行 %s: 他 core からその内部階層を名指すと捕捉される',
    (id, prefix) => {
      // Arrange: 自 core を「表の別の行」にして、この行を**他 core**として名指す。
      const otherRow = MODES.find((m) => m.id !== id);
      const sources = sourcesOf([
        ['root.js', `const P = '${prefix}/js/usecase/period_presets.js';\nexport const l = () => import(P);\n`],
      ]);

      // Act
      const offenders = crossCoreModuleUrlOffenders(sources, '/repo', otherRow.id);

      // Assert: 表に載っている以上、必ず検査対象である（ここが列挙時代に抜け落ちる箇所）。
      expect(offenders).toHaveLength(1);
      expect(offenders[0]).toContain(`${prefix}/js/usecase/period_presets.js`);
    },
  );

  test.each(MODES.map((m) => [m.id, m.prefix]))(
    'モード表の行 %s: 公開面（public/）の名指しは捕捉しない（許可の側も表から導かれる）',
    (id, prefix) => {
      const otherRow = MODES.find((m) => m.id !== id);
      const sources = sourcesOf([
        ['root.js', `const API = '${prefix}/js/public/${id}_public_api.js';\nexport const l = () => import(API);\n`],
      ]);

      expect(crossCoreModuleUrlOffenders(sources, '/repo', otherRow.id)).toEqual([]);
    },
  );

  test('coreSegmentOf は表の全 prefix を core と認め、表に無い語は認めない', () => {
    for (const row of MODES) {
      expect(coreSegmentOf(`${row.prefix}/js/public/x.js`)).toBe(row.id);
    }
    expect(coreSegmentOf(`/${ABSENT_CORE}/js/public/x.js`)).toBeNull();
  });

  // --- 2. 導出が広すぎない ------------------------------------------------------------- //
  test('表に無い core 名は検査面に現れない（導出が広すぎて誤検出の器にならない）', () => {
    const sources = sourcesOf([
      ['root.js', `const P = '/${ABSENT_CORE}/js/usecase/x.js';\nexport const l = () => import(P);\n`],
    ]);

    expect(crossCoreModuleUrlOffenders(sources, '/repo', MODES[0].id)).toEqual([]);
  });

  // --- 3. 表へ 1 行足すと検査面が伸びる（D-11 の中身そのもの）--------------------------- //
  test('モード表へ 1 行足すと検査面が伸びる（列挙時代はここが死んでいた）', () => {
    // Arrange: 現行の表と、5 行目を足した表。導出は同じ 1 本の関数を通す。
    const url = `/${ABSENT_CORE}/js/usecase/x.js`;
    const nowRe = buildCrossCoreModuleUrlRe(coreSegmentsFromPrefixes(MODE_PREFIXES));
    const grownRe = buildCrossCoreModuleUrlRe(
      coreSegmentsFromPrefixes([...MODE_PREFIXES, `/${ABSENT_CORE}`]),
    );

    // Assert: 足す前は見えず、足した後は見える＝検査面が表に追随している。
    expect(url.match(nowRe)).toBeNull();
    expect(url.match(grownRe)).toEqual([url]);
    // 公開面の許可式も同じ経路で伸びる（片側だけ伸びると新 core の public が越境扱いになる）。
    expect(buildPublicUrlRe(coreSegmentsFromPrefixes(MODE_PREFIXES))
      .test(`/${ABSENT_CORE}/js/public/x.js`)).toBe(false);
    expect(buildPublicUrlRe(coreSegmentsFromPrefixes([...MODE_PREFIXES, `/${ABSENT_CORE}`]))
      .test(`/${ABSENT_CORE}/js/public/x.js`)).toBe(true);
  });

  test('検査面は表の prefix から導かれている（第 2 の列挙を持っていない）', () => {
    // prefix の第 1 セグメントと 1:1・表の順。ここが崩れる＝どこかに写しが復活している。
    expect(CORE_SEGMENTS).toEqual(MODE_PREFIXES.map((p) => p.slice(1)));
    expect(CROSS_CORE_MODULE_URL_RE.source)
      .toBe(buildCrossCoreModuleUrlRe(coreSegmentsFromPrefixes(MODE_PREFIXES)).source);
  });

  // --- 4. 形の壊れた prefix は黙って読み替えない ---------------------------------------- //
  test('第 1 セグメントに落ちない prefix は throw する（無音で検査面から落とさない）', () => {
    for (const bad of ['/live/js', 'live', '/', '', '/live/']) {
      expect(() => coreSegmentsFromPrefixes([bad])).toThrow(/モード prefix/);
    }
  });

  test('正規表現メタ文字を含む prefix は throw する（検査式そのものを壊させない）', () => {
    // 検出力の実証: 素通ししたら何が起きるかを合成で示す。名前は選択肢へ**そのまま**埋まるので、
    //   `.*` を含む名前が通ると `/どんな/パス.js` でも当たる＝検査が「全件違反」を吐く器に化ける。
    const naive = buildCrossCoreModuleUrlRe(['live', '.*']);
    expect(`/${ABSENT_CORE}/js/usecase/x.js`.match(naive)).not.toBeNull();

    for (const bad of ['/a|b', '/.*', '/li ve', '/live.']) {
      expect(() => coreSegmentsFromPrefixes([bad])).toThrow(/モード prefix/);
    }
  });

  // --- 5. 計算量: 走査中に正規表現を組み直さない（絶対命令）----------------------------- //
  //
  // 数えるのは時間ではなく**発行回数**。core 名の導出と 2 本の正規表現の組み立ては import 時に
  // 1 度だけ済んでおり、走査の入力（ファイル数・検査項目数）をいくら増やしても再発行は 0 である。
  // 固定するのは「無駄の不在」であって、正規表現を何本持つかという実装詳細ではない。

  /** 走査中の `RegExp` 構築を数える（Test Spy）。リテラルは構築子を通らないので雑音が乗らない。 */
  const countingRegExpConstructions = (run) => {
    const real = globalThis.RegExp;
    let built = 0;
    const spy = new Proxy(real, {
      construct(target, args, newTarget) {
        built += 1;
        return Reflect.construct(target, args, newTarget);
      },
      apply(target, thisArg, args) {
        built += 1;
        return Reflect.apply(target, thisArg, args);
      },
    });
    globalThis.RegExp = spy;
    try {
      run();
    } finally {
      globalThis.RegExp = real;
    }
    return built;
  };

  const syntheticSources = (fileCount) => sourcesOf(
    Array.from({ length: fileCount }, (_, i) => [
      `m${i}.js`,
      `const P${i} = '${MODES[0].prefix}/js/usecase/x${i}.js';\nexport const l${i} = () => import(P${i});\n`,
    ]),
  );

  test.each([30, 60])(
    '走査 %i 本: 発行した正規表現 − 走査に必要な数 = 0（入力を増やしても増えない）',
    (fileCount) => {
      const sources = syntheticSources(fileCount);
      const built = countingRegExpConstructions(() => {
        crossCoreModuleUrlOffenders(sources, '/repo', MODES[1].id);
      });

      expect(built).toBe(0);
    },
  );

  test.each([3, 6])(
    '検査項目を %i 件に増やしても正規表現の発行は 0（オーダーの表明）',
    (itemCount) => {
      const sources = syntheticSources(30);
      const built = countingRegExpConstructions(() => {
        for (let i = 0; i < itemCount; i += 1) {
          crossCoreModuleUrlOffenders(sources, '/repo', MODES[1].id);
        }
      });

      expect(built).toBe(0);
    },
  );

  test('計算量ゲートの検出力: 走査ごとに組み直す変異は赤になる', () => {
    // 「core 名の導出と正規表現の組み立てを走査の内側でやる」実装への変異を再現する。
    const wasteful = (sources) => {
      const out = [];
      for (const [, body] of sources) {
        const re = buildCrossCoreModuleUrlRe(coreSegmentsFromPrefixes(MODE_PREFIXES));
        out.push(...body.match(re) ?? []);
      }
      return out;
    };

    const built30 = countingRegExpConstructions(() => wasteful(syntheticSources(30)));
    const built60 = countingRegExpConstructions(() => wasteful(syntheticSources(60)));

    expect(built30).toBeGreaterThan(0);
    // 入力に比例して増える＝オーダーの違いを本ゲートが見分けられる（空振りしていない）。
    expect(built60).toBeGreaterThan(built30);
  });
});
