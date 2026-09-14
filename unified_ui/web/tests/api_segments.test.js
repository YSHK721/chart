// api_segments.test.js — API セグメント単一表（D-1 是正）の機構検証。
//
// 固定する機構:
//   1) 表の不変条件（セグメント名は [a-z0-9_] のみ・凍結済み）——正規表現へ無エスケープで
//      埋め込む前提を宣言でなく検査で守る。
//   2) isApiSegment（sw_rewrite の判定）と apiUrlPattern（op_log の判定）が同じ表の導出で
//      あること＝全セグメントについて判定が一致すること（導出の同義性）。
//   3) 乖離バグ本体の再発防止: op_log の fetch 記録が tf_period_profile を対象にすること、
//      実在しない compute_seq を対象にしないこと。
//   4) 写しの再発防止（機械的検査）: sw_rewrite.js / op_log.js のソースにセグメント名の
//      リテラル列挙が復活していないこと。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, it, expect } from 'vitest';
import {
  API_SEGMENTS, API_SEGMENT_PREFIXES, isApiSegment, apiUrlPattern,
} from '../js/api_segments.js';
import { rewritePath } from '../js/sw_rewrite.js';
import { installOpLog } from '../js/op_log.js';

const srcOf = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');

describe('表の不変条件', () => {
  it('セグメント名は [a-z0-9_] のみ（正規表現へ無エスケープで埋め込める）', () => {
    for (const s of [...API_SEGMENTS, ...API_SEGMENT_PREFIXES]) {
      expect(s).toMatch(/^[a-z0-9_]+$/);
    }
  });

  it('表は凍結されている（実行時の書換えで消費者間がずれない）', () => {
    expect(Object.isFrozen(API_SEGMENTS)).toBe(true);
    expect(Object.isFrozen(API_SEGMENT_PREFIXES)).toBe(true);
  });
});

describe('導出の同義性（sw_rewrite 判定 ⇔ op_log 判定）', () => {
  it('表の全セグメントは両判定で API になる', () => {
    const re = apiUrlPattern();
    for (const s of API_SEGMENTS) {
      expect(isApiSegment(s)).toBe(true);
      expect(re.test(`/${s}?x=1`)).toBe(true);
      expect(re.test(`/live/${s}?x=1`)).toBe(true);
      // sw_rewrite がリライトする対象は op_log でも必ず記録対象（乖離の一般形を遮断）。
      expect(re.test(rewritePath('live', `/${s}?x=1`))).toBe(true);
    }
  });

  it('前方一致族（market_profile 系）も両判定で API になる', () => {
    const re = apiUrlPattern();
    for (const fam of ['market_profile', 'market_profile_forming']) {
      expect(isApiSegment(fam)).toBe(true);
      expect(re.test(`/${fam}?bins=30`)).toBe(true);
    }
  });

  it('非 API（静的資産）は両判定で対象外', () => {
    const re = apiUrlPattern();
    for (const path of ['/', '/index.html', '/js/op_log.js', '/vendor/x.js', '/sw.js']) {
      const seg = path.slice(1).split(/[/?#]/)[0];
      expect(isApiSegment(seg)).toBe(false);
      expect(re.test(path)).toBe(false);
    }
  });
});

describe('乖離バグ本体（D-1）の再発防止', () => {
  const makeWin = () => {
    const win = {
      performance: { now: () => 0 },
      fetch: () => Promise.resolve({ ok: true, status: 200 }),
      console: { error: () => {}, log: () => {} },
      addEventListener: () => {},
      removeEventListener: () => {},
      sessionStorage: { setItem: () => {}, getItem: () => null },
    };
    const doc = {
      body: { className: '' },
      addEventListener: () => {},
      removeEventListener: () => {},
    };
    return { win, doc };
  };

  it('op_log は /tf_period_profile の fetch を記録する（欠落していた本体）', async () => {
    const { win, doc } = makeWin();
    const api = installOpLog({ win, doc });
    const before = api.log.size();
    await win.fetch('/tf_period_profile?datasetRef=jp225_tick&timeframe=1D');
    expect(api.log.size()).toBe(before + 1);
  });

  it('op_log は実在しない /compute_seq を対象にしない', async () => {
    const { win, doc } = makeWin();
    const api = installOpLog({ win, doc });
    const before = api.log.size();
    await win.fetch('/compute_seq?x=1');
    expect(api.log.size()).toBe(before);
  });
});

describe('写しの再発防止（機械的検査）', () => {
  // 表の中身を名指しできるのは api_segments.js だけ。消費者ソースにセグメント名リテラルが
  //   復活したら（= 第 2 の台帳の芽）ここで落とす。mode_table 由来語などは対象外の集合で判定。
  it('sw_rewrite.js / op_log.js にセグメント名のリテラルが無い', () => {
    const consumers = ['../js/sw_rewrite.js', '../js/op_log.js'];
    // tf_period_profile は sw_rewrite の LIVE_ONLY_SEGMENTS（ルーティング方針の表・別関心）で
    //   名指しを許す。それ以外のセグメント名は全消費者で禁止。
    const allowed = new Map([['../js/sw_rewrite.js', new Set(['tf_period_profile'])]]);
    for (const rel of consumers) {
      const src = srcOf(rel)
        // コメント行は対象外（経緯の説明でセグメント名に触れてよい）。
        .split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');
      for (const s of API_SEGMENTS) {
        if (allowed.get(rel)?.has(s)) continue;
        expect(src.includes(`'${s}'`) || src.includes(`"${s}"`) || src.includes(`\`${s}\``),
          `${rel} がセグメント '${s}' をリテラルで名指ししている（api_segments.js から導出せよ）`)
          .toBe(false);
      }
    }
  });
});
