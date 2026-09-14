// MP 列＝ライブ MP の借用（第 1 段階）— 合成根での結線と dormant の機械的告知。
//
// 設計入力（依頼者承認 2026-09-06「窓を同期したらよいのではないか」→ 承認された解）:
//   ラダーの MP は live core の `/market_profile` を、**ライブチャートと同一のパラメータで**
//   借用する（ローソク借用＝candles_client / ISSUE-470 と同型）。これで窓・仕様・更新規則・
//   計算（サーバ側メモ化込み）がライブと自動同期する。
//
// 第 1 段階は**フロントだけ**を切り替える。サーバ側 MP 供給（`/reach_sheet` の `mp` 欄・
//   MarketProfileGateway）は無改変で温存する（dormant）。したがって「版面がもう `mp` 欄を
//   読んでいない」ことを機械的に告知する必要がある——読んでいないつもりで読んでいると、
//   第 2 段階（撤去）で初めて版面が壊れる。ここでは 3 つの角度から固定する:
//     (a) reach_sheet_view.js のソースに `row.mp` 参照が 1 つも無い
//     (b) 応答が `mp` を運んでも、借用が無ければ版面にバーは出ない
//     (c) 応答の `mp` と借用 profile が食い違えば、**借用側**が版面に出る
//
// 計算量テスト（CLAUDE.md 絶対命令 §4.1）: fetch を Test Spy にして
//   - バー枠が進まない限り MP の発行は 0（≥2 点）
//   - 枠が進んだら次の 1 回だけ
//   - 借用した profile は 1 本残らず描画に使われる（発行 − 使用 = 0）
//   - ラダーの行数を増やしても発行は増えない（2 点固定）
//   を表明する。回数そのものは期待値に焼き込まない。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { fakeDoc, fakeEl, flatten, sheetResponse, ladderRow } from './_fake_dom.js';
import { TEMPLATE_STORAGE_KEYS } from '../js/adapter/front/template_binding_reader.js';
import { setupDashboardDisplay } from '../js/adapter/front/composition_root_front.js';
// live core の**公開面**から借りる実物（合成根が実行時 import するものと同じ URL・同じ面）。
//   adapter を直接名指すと、live_public_api.js の再輸出 3 行を消しても本スイートは緑のままで、
//   借用面が壊れたことがどの検定にも映らない（G-3 は dashboard 側の js/ しか走査しない）。
//   ここを公開面に固定することが、再輸出の存在そのものの機械的保証になる。
import {
  buildMarketProfileUrl, MpFetchParams, MP_DEFAULT_SOURCE,
} from '../../../indigators/indicator_ui/web/js/public/live_public_api.js';

const BAR_MS = 60_000;
const PRICE_MIN = 65_000;
const PRICE_MAX = 66_000;
const N_BINS = 10;

/** 借用 profile（bin 幅 100pt・norm は bin ごとに異なる＝引かれた添字が観測できる）。 */
function borrowedProfile(shift = 0) {
  return {
    price_min: PRICE_MIN,
    price_max: PRICE_MAX,
    n_bins: N_BINS,
    bins: Array.from({ length: N_BINS }, (_unused, i) => ({
      price: PRICE_MIN + (i + 0.5) * ((PRICE_MAX - PRICE_MIN) / N_BINS),
      norm: ((i + 1) % (N_BINS + 1)) / N_BINS === 0 ? 0.05 : ((i + 1 + shift) % N_BINS) / N_BINS,
    })),
  };
}

/** 既定行の価格 65803.4 → bin 8（(65803.4-65000)/100 = 8.034）。 */
const ROW_PRICE = 65_803.4;
const ROW_BIN = 8;

/** MP instance を 1 件持つテンプレートを組む。 */
function templateOf(templateId, mpParams) {
  const instances = [
    { indicatorId: 'ma_marod', variant: 'default', params: { length: 5 }, visible: true, styles: null },
  ];
  if (mpParams !== null) {
    instances.push({
      indicatorId: 'market_profile', variant: 'default', params: mpParams, visible: true, styles: null,
    });
  }
  return { templateId, name: templateId, instances };
}

/**
 * live スコープの読み取り専用 storage。
 *
 * `otherMpParams` を与えると、チャート足（1m）**以外**の足へ別テンプレート（別の MP 設定）を
 * 紐付ける。抽出規則（timeframe_binding === CHART_TIMEFRAME の先頭 1 件）が本当に効いて
 * いるかは、設定を**食い違わせないと**観測できない（同じ設定なら条件を消しても緑になる）。
 */
function readOnlyTemplates({
  mpParams = { src: 'zp', va: 0.7 }, withMp = true, otherMpParams = null,
} = {}) {
  const chartTemplate = templateOf('tpl#chart', withMp ? mpParams : null);
  const templates = [chartTemplate];
  const bindings = {
    '1m': 'tpl#chart', '5m': 'tpl#chart', '15m': 'tpl#chart', '1h': 'tpl#chart',
    '4h': 'tpl#chart', '1D': 'tpl#chart', '1W': 'tpl#chart', '1M': 'tpl#chart',
  };
  if (otherMpParams !== null) {
    templates.push(templateOf('tpl#other', otherMpParams));
    for (const tf of ['5m', '15m', '1h', '4h', '1D', '1W', '1M']) {
      bindings[tf] = 'tpl#other';
    }
  }
  const map = {
    [TEMPLATE_STORAGE_KEYS.templates]: JSON.stringify({ templates }),
    [TEMPLATE_STORAGE_KEYS.bindings]: JSON.stringify({ bindings }),
  };
  const refuse = (op) => () => { throw new TypeError(`readOnlyStorage: ${op} は許可されていない`); };
  return {
    getItem: (key) => (Object.prototype.hasOwnProperty.call(map, key) ? map[key] : null),
    key: () => null,
    get length() { return Object.keys(map).length; },
    setItem: refuse('setItem'), removeItem: refuse('removeItem'), clear: refuse('clear'),
  };
}

/**
 * 合成根を手回しの時計で回す試験台。
 *
 * `/reach_sheet`（POST）・`/candles`（GET）・`/market_profile`（GET）を別々に数える。
 */
function harness({
  rows = [ladderRow({ price: ROW_PRICE })],
  mpResponder = () => ({ ok: true, profile: borrowedProfile() }),
  templates = readOnlyTemplates(),
  sheetResponder = null,
  mpDelayTurns = 0,
  loadLiveMpApi = () => Promise.resolve({
    buildMarketProfileUrl, MpFetchParams, MP_DEFAULT_SOURCE,
  }),
} = {}) {
  const doc = fakeDoc();
  const host = fakeEl('div');
  const mpUrls = [];
  const payload = sheetResponse({ rows, current_index: rows.length });
  let sheetCalls = 0;
  let nowMs = 0;

  const fetchFn = (url, init) => {
    if (init && init.body) {
      sheetCalls += 1;
      const answer = sheetResponder ? sheetResponder(sheetCalls) : payload;
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(answer) });
    }
    if (String(url).includes('/market_profile')) {
      mpUrls.push(String(url));
      const answer = mpResponder(mpUrls.length);
      if (answer === null) {
        return Promise.reject(new Error('切断'));
      }
      // 借用は**シート応答より後**に着弾させられるようにする（合成根は MP をシートより
      //   先に発行するため、素の Promise.resolve では常に MP が先に解決してしまい、
      //   「失敗掲示の上に借用が描き戻す」経路が検定から隠れる）。
      const settleAfter = (value, turns) => {
        let p = Promise.resolve(value);
        for (let i = 0; i < turns; i += 1) { p = p.then((v) => v); }
        return p;
      };
      return settleAfter({
        ok: answer.httpOk !== false, status: answer.httpOk === false ? 503 : 200,
        json: () => settleAfter(answer, mpDelayTurns),
      }, mpDelayTurns);
    }
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({ ok: true, candles: [{ time: 60, open: 1, high: 2, low: 0.5, close: ROW_PRICE }] }),
    });
  };

  const setup = setupDashboardDisplay({
    doc,
    host,
    templates,
    fetch: fetchFn,
    apiPrefix: '/dashboard',
    now: () => nowMs,
    schedule: () => () => {},
    barCloseTimeOf: () => Math.floor(nowMs / BAR_MS),
    // live 公開面の実物を注入する（合成根の既定は動的 import・検定はここで差し替える）。
    loadLiveMpApi,
    lwc: null,
  });

  return {
    doc, host, mpUrls, setup,
    advance: (ms) => { nowMs += ms; },
    /** 保留中の microtask（借用の着弾）を流し切る。 */
    flush: async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); },
  };
}

/** 水準行の MP バー（無ければ null）。 */
function mpBarsOf(host) {
  return flatten(host).filter((el) => el.classList && el.classList.contains('dash-ladder-mp-bar'));
}

/** ラダーの掲示欄の文言。 */
function messageOf(host) {
  const el = flatten(host).find((e) => e.classList && e.classList.contains('dash-sheet-message'));
  return el ? el.textContent : null;
}

describe('MP 借用 — 合成根の結線', () => {
  test('the_mp_column_is_painted_from_the_borrowed_live_profile', async () => {
    // Arrange
    const h = harness();
    const handle = await h.setup;
    // Act
    await handle.enable();
    await h.flush();
    // Assert: 行価格 65803.4 は bin 8＝norm 0.9（借用 profile の中身がそのまま出る）。
    const bars = mpBarsOf(h.host);
    assert.equal(bars.length, 1, 'MP のバーが版面にありません');
    assert.equal(bars[0].style.width, `${((ROW_BIN + 1) / N_BINS) * 100}%`);
  });

  test('the_profile_is_borrowed_from_the_live_core_prefix', async () => {
    const h = harness();
    const handle = await h.setup;
    await handle.enable();
    await h.flush();

    assert.ok(h.mpUrls.length > 0, 'MP を 1 度も借りていません');
    // ローソク借用（candles_client）と同じ prefix 規約＝ live core の面から取る。
    assert.ok(
      h.mpUrls.every((url) => url.startsWith('/live/market_profile?')),
      `live core の面を叩いていません: ${h.mpUrls[0]}`,
    );
  });

  test('the_borrowed_url_carries_the_chart_timeframe_and_the_template_settings', async () => {
    // 借用の意味は「ライブと同一パラメータ」。テンプレートで変えた設定がそのまま乗る。
    const h = harness({ templates: readOnlyTemplates({ mpParams: { src: 'zp', va: 0.62 } }) });
    const handle = await h.setup;
    await handle.enable();
    await h.flush();

    const url = h.mpUrls[0];
    assert.match(url, /datasetRef=jp225_mt5/);   // 台帳の既定（ISSUE-512 段階 4）
    assert.match(url, /timeframe=1m/);
    assert.match(url, /src=zp/);
    assert.match(url, /va=0\.62/);
  });

  test('the_chart_timeframe_binding_supplies_the_mp_settings', async () => {
    // 抽出規則（裁定 7）: `timeframe_binding === CHART_TIMEFRAME` の先頭 1 件。
    //   これは規則の**正の経路**（チャート足の設定が実際に URL へ乗る）を見る。
    const h = harness({
      templates: readOnlyTemplates({
        mpParams: { src: 'zp', va: 0.61 },           // 1m（チャート足）
        otherMpParams: { src: 'dwell', va: 0.99 },   // 5m 以降
      }),
    });
    const handle = await h.setup;
    await handle.enable();
    await h.flush();

    const url = h.mpUrls[0];
    assert.match(url, /va=0\.61/, 'チャート足の MP 設定が使われていません');
    assert.doesNotMatch(url, /va=0\.99/, 'チャート足以外の MP 設定が混ざっています');
  });

  test('an_mp_bound_only_to_other_timeframes_never_supplies_the_ladder', async () => {
    // 抽出規則の**条件そのもの**を固定する検定。
    //
    // なぜ「1m と 5m に違う設定」では足りないか（実測 2026-09-06）: 束は
    //   DASHBOARD_TIMEFRAMES の順（1m が先頭）で組まれる（template_binding_reader.js:126）。
    //   チャート足に MP がある限り「束の先頭の market_profile」＝「チャート足の
    //   market_profile」なので、`timeframe_binding` の条件を**消しても同じ結果**になる
    //   ——条件を検査できない（vacuous）。
    //   条件が効くのは「チャート足に MP が無く、他の足にはある」場合だけである:
    //     条件あり → 該当なし → ライブ既定 src へ縮退
    //     条件なし → 5m の MP（src=dwell・va=0.99）を誤って borrow
    const h = harness({
      templates: readOnlyTemplates({
        withMp: false,                               // 1m（チャート足）には MP を置かない
        otherMpParams: { src: 'dwell', va: 0.99 },   // 5m 以降にだけ MP がある
      }),
    });
    const handle = await h.setup;
    await handle.enable();
    await h.flush();

    const url = h.mpUrls[0];
    assert.match(url, new RegExp(`src=${MP_DEFAULT_SOURCE}`), 'ライブ既定へ縮退していません');
    assert.doesNotMatch(url, /src=dwell/, 'チャート足に紐付いていない MP を借用しています');
    assert.doesNotMatch(url, /va=0\.99/, 'チャート足に紐付いていない MP を借用しています');
  });

  test('a_template_without_market_profile_still_borrows_with_the_live_default_source', async () => {
    // テンプレートに MP が無い場合の規則（裁定 7）: src=MP_DEFAULT_SOURCE だけを付ける。
    //   リテラル 'zp' を版面側に書かない（ライブが既定を変えたら追随する）。
    const h = harness({ templates: readOnlyTemplates({ withMp: false }) });
    const handle = await h.setup;
    await handle.enable();
    await h.flush();

    assert.ok(h.mpUrls.length > 0);
    assert.match(h.mpUrls[0], new RegExp(`src=${MP_DEFAULT_SOURCE}`));
  });

  test('a_level_outside_the_profile_range_draws_no_bar_instead_of_the_edge_density', async () => {
    // 境界値: プロファイルの価格域の外。端の bin へ丸めると「外」だと分からなくなる。
    const rows = [
      ladderRow({ price: ROW_PRICE }),
      ladderRow({ price: PRICE_MAX + 500, label: 'far' }),
    ];
    const h = harness({ rows });
    const handle = await h.setup;
    await handle.enable();
    await h.flush();

    assert.equal(mpBarsOf(h.host).length, 1, '範囲外の行にもバーが出ています');
  });

  test('a_failed_borrow_is_posted_on_the_sheet_instead_of_a_silent_blank', async () => {
    // 無言縮退の禁止（設計書 §5.2 / §7 と同じ規約）。借りられないことが読み取れる形で出る。
    const h = harness({ mpResponder: () => null });
    const handle = await h.setup;
    await handle.enable();
    await h.flush();

    assert.equal(mpBarsOf(h.host).length, 0);
    assert.match(String(messageOf(h.host)), /MP/);
  });

  test('a_borrowed_profile_never_repaints_over_a_failing_sheet', async () => {
    // 借用の着弾は**シートの失敗掲示を消してはならない**。合成根は直近の完全応答を
    //   持っているので、無条件に描き戻すと「取得できていない」はずの版面へ古い行が戻り、
    //   理由の掲示も消える（ユーザーには復旧したように見える＝最も危険な縮退）。
    const good = sheetResponse({ rows: [ladderRow({ price: ROW_PRICE })], current_index: 1 });
    const bad = { ok: false, error: { type: 'supply', message: 'シートを取得できません' } };
    const h = harness({
      sheetResponder: (n) => (n === 1 ? good : bad),
      mpResponder: () => ({ ok: true, profile: borrowedProfile() }),
      // 合成根は MP をシートより**先に**発行する。借用の着弾を遅らせて、
      //   「失敗掲示が出た後に借用が返る」順序を確定させる（順序任せにしない）。
      mpDelayTurns: 4,
    });
    const handle = await h.setup;
    await handle.enable();
    await h.flush();

    // 2 巡目: シートが失敗し、その後に借用が着弾する。
    h.advance(BAR_MS);
    await handle.refresh();
    await h.flush();

    assert.match(String(messageOf(h.host)), /シートを取得できません/, '失敗掲示が消えています');
    assert.equal(mpBarsOf(h.host).length, 0, '失敗中の版面へ古い行が描き戻されています');
  });

  test('an_unusable_live_public_face_is_posted_instead_of_failing_silently', async () => {
    // 公開面が読めない・面の形が違う（再輸出の削除・live 停止・配置換え）とき、
    //   従来は catch と早期 return で**完全に無言**だった。MP 列がただ空になるだけで、
    //   借用が始まってすらいないことが版面からも検定からも読めない。
    for (const loader of [
      () => Promise.reject(new Error('404')),
      () => Promise.resolve({}),                       // 面はあるが名前が無い（再輸出の削除）
      () => Promise.resolve({ buildMarketProfileUrl }),  // 片方だけ在る
    ]) {
      const h = harness({ loadLiveMpApi: loader });
      const handle = await h.setup;
      await handle.enable();
      await h.flush();

      assert.equal(h.mpUrls.length, 0, '借用できないのに発行しています');
      assert.match(
        String(messageOf(h.host)), /MP/,
        '公開面を読めないことが版面に掲示されていません',
      );
    }
  });
});

describe('MP 借用 — dormant の機械的告知（第 2 段階で撤去する側）', () => {
  test('the_ladder_view_source_never_reads_the_mp_field_of_the_response', async () => {
    // (a) ソース走査。`/reach_sheet` の `mp` 欄は版面から読まれない。
    const source = readFileSync(
      fileURLToPath(new URL('../js/adapter/front/reach_sheet_view.js', import.meta.url)), 'utf8',
    );
    const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');
    const reads = [...code.matchAll(/\brow\s*\.\s*mp\b/g)].map((m) => m[0]);
    assert.deepEqual(
      reads, [],
      '版面が /reach_sheet の mp 欄を読んでいます（借用へ切り替わっていない）',
    );
  });

  test('a_response_carrying_mp_paints_nothing_when_nothing_was_borrowed', async () => {
    // (b) 応答が `mp` を運んでも、借用が無ければバーは出ない＝欄は死んでいる。
    const h = harness({
      rows: [ladderRow({ price: ROW_PRICE, mp: 0.25 })],
      mpResponder: () => ({ ok: false, error: { message: 'まだ無い' } }),
    });
    const handle = await h.setup;
    await handle.enable();
    await h.flush();

    assert.equal(mpBarsOf(h.host).length, 0, '応答の mp 欄からバーが描かれています');
  });

  test('the_borrowed_profile_wins_when_it_disagrees_with_the_response_mp_field', async () => {
    // (c) 食い違わせて、どちらが版面に出ているかを一意に決める。
    const h = harness({ rows: [ladderRow({ price: ROW_PRICE, mp: 0.25 })] });
    const handle = await h.setup;
    await handle.enable();
    await h.flush();

    const bars = mpBarsOf(h.host);
    assert.equal(bars.length, 1);
    assert.equal(bars[0].style.width, `${((ROW_BIN + 1) / N_BINS) * 100}%`);
    assert.notEqual(bars[0].style.width, '25%', '応答の mp 欄が版面に出ています');
  });

  test('the_mp_tooltip_names_the_live_chart_instead_of_a_window_length', async () => {
    // 裁定 8(b): 窓の名乗りはライブ同期になったので本数の焼き込みを廃止する。
    //   サーバ側の窓（MP_WINDOW_BARS=2000）はもう版面の窓ではない。
    const h = harness();
    const handle = await h.setup;
    await handle.enable();
    await h.flush();

    const cell = flatten(h.host).find((el) => el.tagName === 'TD' && el.dataset && el.dataset.cell === 'mp');
    assert.ok(cell, 'MP のセルがありません');
    assert.match(cell.title, /ライブチャート/);
    assert.doesNotMatch(cell.title, /2000/, 'サーバ側の窓の本数を名乗り続けています');

    const source = readFileSync(
      fileURLToPath(new URL('../js/adapter/front/reach_sheet_view.js', import.meta.url)), 'utf8',
    );
    assert.equal(
      (source.match(/(?<![\w.])2000(?![\w.])/g) ?? []).length, 0,
      '版面にサーバ側の窓の本数が残っています',
    );
  });
});

describe('MP 借用 — 計算量（絶対命令 §4.1）', () => {
  test('no_bar_boundary_means_no_further_borrow', async () => {
    // ≥2 点で「枠が進まなければ発行 0」を観測する。
    const h = harness();
    const handle = await h.setup;
    await handle.enable();
    await h.flush();
    const after = h.mpUrls.length;

    h.advance(1_000);
    await handle.refresh();
    await h.flush();
    assert.equal(h.mpUrls.length, after, '枠の内側で借用が増えています（1 点目）');

    h.advance(30_000);
    await handle.refresh();
    await h.flush();
    assert.equal(h.mpUrls.length, after, '枠の内側で借用が増えています（2 点目）');
  });

  test('crossing_a_bar_boundary_borrows_the_next_one_and_only_one', async () => {
    const h = harness();
    const handle = await h.setup;
    await handle.enable();
    await h.flush();
    const after = h.mpUrls.length;

    h.advance(BAR_MS);
    await handle.refresh();
    await h.flush();
    assert.equal(h.mpUrls.length, after + 1, '枠が進んだのに借りていません');

    for (let i = 0; i < 10; i += 1) {
      h.advance(1_000);
      await handle.refresh();
      await h.flush();
    }
    assert.equal(h.mpUrls.length, after + 1, '同じ枠で 2 回目を借りています');
  });

  test('every_borrowed_profile_is_used_for_exactly_one_ladder_paint', async () => {
    // 発行 − 使用 = 0。描き直しは**新しい要素**を作るので、要素の同一性を数えれば
    //   「借りたのに描かなかった」も「借りていないのに描き直した」も両方捕まる。
    const h = harness({ mpResponder: (n) => ({ ok: true, profile: borrowedProfile(n) }) });
    const handle = await h.setup;
    await handle.enable();
    await h.flush();

    const painted = new Set(mpBarsOf(h.host));
    for (let bar = 0; bar < 4; bar += 1) {
      h.advance(BAR_MS);
      await handle.refresh();
      await h.flush();
      mpBarsOf(h.host).forEach((el) => painted.add(el));
    }

    assert.ok(h.mpUrls.length > 1, '借用が 1 本以下です（検定が空振り）');
    assert.equal(
      painted.size, h.mpUrls.length,
      `借りた profile ${h.mpUrls.length} 本に対し描画は ${painted.size} 回（差は作って捨てた計算）`,
    );
  });

  test('the_number_of_ladder_rows_does_not_change_the_number_of_borrows', async () => {
    // オーダーの表明（2 点固定）: 発行は行数で決まらない（1 プロファイルで全行を引く）。
    const borrowsFor = async (rowCount) => {
      const rows = Array.from({ length: rowCount }, (_unused, i) => ladderRow({
        price: PRICE_MIN + i * 7, label: `L${i}`, instance_key: ['cvfe', 'default', '{}', '5m'],
      }));
      const h = harness({ rows });
      const handle = await h.setup;
      await handle.enable();
      await h.flush();
      for (let bar = 0; bar < 3; bar += 1) {
        h.advance(BAR_MS);
        await handle.refresh();
        await h.flush();
      }
      return h.mpUrls.length;
    };
    assert.equal(await borrowsFor(1), await borrowsFor(20));
  });

  test('disable_stops_every_further_borrow', async () => {
    // モードを出た後に叩き続けない。
    const h = harness();
    const handle = await h.setup;
    await handle.enable();
    await h.flush();
    const after = h.mpUrls.length;

    await handle.disable();
    h.advance(BAR_MS * 5);
    await handle.refresh();
    await h.flush();

    assert.equal(h.mpUrls.length, after);
  });
});
