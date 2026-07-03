// market_profile_client.js の純ロジック検証（URL 組み立て・応答整形・fetch 失敗時 null）。
//
// 設計入力: 依頼「取得ロジックの単体テスト（URL組み立て・応答→primitiveデータ整形）」。
//   Backend 契約: GET /market_profile?datasetRef=&timeframe=&limit=&bins=&va=
//   応答 {ok:true, profile:{bins:[{price,tpo,norm}],poc,va_low,va_high,price_min,price_max,tpo_units,n_bins}}
//   失敗時 {ok:false, error:{...}}。DOM/chart/実 fetch 非依存（Fake fetch を注入）。
// 構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildMarketProfileUrl,
  parseProfileResponse,
  MarketProfileClient,
} from '../js/adapter/front/market_profile_client.js';

const OK_PAYLOAD = {
  ok: true,
  profile: {
    bins: [{ price: 100, tpo: 2, norm: 0.5 }, { price: 101, tpo: 4, norm: 1 }],
    poc: 101, va_low: 100, va_high: 101, price_min: 100, price_max: 101,
    tpo_units: 6, n_bins: 2,
  },
};

test('buildMarketProfileUrl encodes datasetRef and always includes it', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick' });
  // Assert
  assert.equal(url, '/market_profile?datasetRef=jp225_tick');
});

test('buildMarketProfileUrl appends timeframe/bins/va and NEVER limit (全期間集計固定)', () => {
  // Arrange / Act: limit を渡しても URL には付与しない（全期間集計＝limit 非送信）。
  const url = buildMarketProfileUrl({
    datasetRef: 'sample', timeframe: '1D', limit: 1500, bins: 24, va: 0.7,
  });
  // Assert: 各パラメータが URL に含まれるが limit= は決して含まれない
  assert.ok(url.startsWith('/market_profile?datasetRef=sample'));
  assert.ok(url.includes('&timeframe=1D'));
  assert.ok(!url.includes('limit='), 'limit は送らない（全期間集計）');
  assert.ok(url.includes('&bins=24'));
  assert.ok(url.includes('&va=0.7'));
});

test('buildMarketProfileUrl omits optional params when null/undefined', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'sample', timeframe: null, limit: undefined });
  // Assert: 省略パラメータは付かない
  assert.equal(url, '/market_profile?datasetRef=sample');
});

test('buildMarketProfileUrl appends src when provided (src=dwell)', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', src: 'dwell' });
  // Assert
  assert.ok(url.includes('&src=dwell'));
});

test('buildMarketProfileUrl omits src when not provided (candle 後方互換=URLに付けない)', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'sample' });
  // Assert: src 省略時は URL に付与しない（サーバ既定 candle）
  assert.ok(!url.includes('src='));
  assert.equal(url, '/market_profile?datasetRef=sample');
});

// リプレイ時間カーソル（to）: 指定時のみ &to=<UNIX秒> を付与、省略時は付けない（後方互換）。
//   移植元 prototype_260630-01（as-seen-at-t・アンカー）。
test('buildMarketProfileUrl appends &to when provided (replay time cursor)', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', to: 1277856000 });
  // Assert
  assert.ok(url.includes('&to=1277856000'));
});

test('buildMarketProfileUrl omits &to when not provided (省略時=全期間・後方互換)', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'sample' });
  // Assert: to 省略時は URL に付与しない（サーバ既定=全期間）
  assert.ok(!url.includes('to='));
  assert.equal(url, '/market_profile?datasetRef=sample');
});

test('buildMarketProfileUrl omits &to when to is null', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'sample', to: null });
  // Assert
  assert.ok(!url.includes('to='));
});

// ローリング窓（from）: 指定時のみ &from=<UNIX秒> を付与、省略/null は付けない（後方互換）。
//   移植元 prototype_260630-01（ローリング窓 = T-ROLL_BARS本）。増分2 A。
test('buildMarketProfileUrl appends &from when provided (rolling window lower bound)', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', from: 1277000000, to: 1277856000 });
  // Assert: from/to の両方が付く（ローリング窓 [from,to]）
  assert.ok(url.includes('&from=1277000000'));
  assert.ok(url.includes('&to=1277856000'));
});

test('buildMarketProfileUrl omits &from when not provided or null (省略時=全期間・後方互換)', () => {
  // Arrange / Act
  assert.ok(!buildMarketProfileUrl({ datasetRef: 'sample' }).includes('from='));
  assert.ok(!buildMarketProfileUrl({ datasetRef: 'sample', from: null }).includes('from='));
});

// スナップショット（today）: today===true のとき &today=1 を付与、false/未指定は付けない（後方互換）。
//   移植元 prototype_260630-01（?today=1 で today[]/today_max）。増分2 C。
test('buildMarketProfileUrl appends &today=1 when today is true (snapshot)', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', today: true });
  // Assert
  assert.ok(url.includes('&today=1'));
});

test('buildMarketProfileUrl omits &today when false/undefined (snapshot OFF・後方互換)', () => {
  // Arrange / Act
  assert.ok(!buildMarketProfileUrl({ datasetRef: 'sample' }).includes('today='));
  assert.ok(!buildMarketProfileUrl({ datasetRef: 'sample', today: false }).includes('today='));
});

// parseProfileResponse: today[]/today_max を profile へ素通し（スナップショット primitive が消費）。
test('parseProfileResponse passes through today[]/today_max when present', () => {
  // Arrange
  const payload = {
    ok: true,
    profile: {
      bins: [{ price: 100, tpo: 1, norm: 1 }], poc: 100, va_low: 100, va_high: 100,
      price_min: 100, price_max: 101, tpo_units: 1, n_bins: 1,
      today: [0.5], today_max: 0.5,
    },
  };
  // Act
  const out = parseProfileResponse(payload);
  // Assert: today/today_max が profile に保持される
  assert.deepEqual(out.today, [0.5]);
  assert.equal(out.today_max, 0.5);
});

// 解像度モード（resmode）で bins/barw の送信を排他化する（試作 prototype_260630-01 の解像度トグル移植）。
test('buildMarketProfileUrl with resmode=range appends &barw=<range> and omits bins', () => {
  // Arrange / Act: 解像度=レンジ → range を backend param barw へ写像、bins は送らない。
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', resmode: 'range', range: '50', bins: 60 });
  // Assert
  assert.ok(url.includes('&barw=50'));
  assert.ok(!url.includes('bins='));
});

test('buildMarketProfileUrl with resmode=bins appends &bins and omits barw', () => {
  // Arrange / Act: 解像度=ビン → bins を送り、range があっても barw は送らない。
  const url = buildMarketProfileUrl({ datasetRef: 'sample', resmode: 'bins', bins: 30, range: '100' });
  // Assert
  assert.ok(url.includes('&bins=30'));
  assert.ok(!url.includes('barw='));
});

// bins は ENUM プリセット化に伴い文字列（'30'/'60'/'100'）で渡る。resmode=bins（またはトグル未指定）で
//   文字列プリセットを &bins=<値> として付与する（backend の _parse_int が '60' を解釈可）。
test('buildMarketProfileUrl with resmode=bins appends &bins for a string preset (ENUM)', () => {
  // Arrange / Act: ENUM プリセット文字列 '60' を渡す。
  const url = buildMarketProfileUrl({ datasetRef: 'sample', resmode: 'bins', bins: '60', range: '100' });
  // Assert: 文字列プリセットでも &bins=60 が付与され、barw は送らない。
  assert.ok(url.includes('&bins=60'));
  assert.ok(!url.includes('barw='));
});

test('buildMarketProfileUrl appends &bins for a string preset even when resmode is absent (トグル未指定)', () => {
  // Arrange / Act: resmode 未指定（既定 bins 相当）でも文字列プリセットを送る。
  const url = buildMarketProfileUrl({ datasetRef: 'sample', bins: '30' });
  // Assert
  assert.ok(url.includes('&bins=30'));
  assert.ok(!url.includes('barw='));
});

test('buildMarketProfileUrl omits bins for an empty string (未選択ガード)', () => {
  // Arrange / Act: 空文字は無効な &bins= を送出しない。
  const url = buildMarketProfileUrl({ datasetRef: 'sample', resmode: 'bins', bins: '' });
  // Assert
  assert.ok(!url.includes('bins='));
});

test('buildMarketProfileUrl omits barw when resmode is absent (既定 bins 相当)', () => {
  // Arrange / Act: resmode 未指定は bins 相当（barw 非送信）。
  const url = buildMarketProfileUrl({ datasetRef: 'sample', bins: 30 });
  // Assert
  assert.ok(!url.includes('barw='));
  assert.ok(url.includes('&bins=30'));
});

test('buildMarketProfileUrl omits bins when bins is non-finite under resmode=bins (NaN 貼付ガード)', () => {
  // Arrange/Act: NaN（貼付等で数値化に失敗した値）が bins に渡っても無効な &bins=NaN を送出しない。
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', resmode: 'bins', bins: NaN });
  // Assert: bins= は付かない。
  assert.ok(!url.includes('bins='));
});

test('buildMarketProfileUrl still appends bins when bins is a finite number (正常系不変)', () => {
  // Arrange/Act: 有限 bins は従来通り URL に付与される（正常系の非回帰）。
  const url = buildMarketProfileUrl({ datasetRef: 'sample', bins: 24 });
  // Assert
  assert.ok(url.includes('&bins=24'));
});

test('buildMarketProfileUrl appends src=m1 when provided (tick数)', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', src: 'm1' });
  // Assert: m1 も既存 src 経路で URL に載る
  assert.ok(url.includes('&src=m1'));
});

test('parseProfileResponse passes through src/atom when present, else no extra keys', () => {
  // Arrange
  const withMeta = { ok: true, profile: { bins: [], poc: 1 }, src: 'dwell', atom: 'tick滞在秒' };
  // Act
  const p = parseProfileResponse(withMeta);
  // Assert: src/atom を素通し・既存キー維持
  assert.equal(p.src, 'dwell');
  assert.equal(p.atom, 'tick滞在秒');
  assert.equal(p.poc, 1);
  // src/atom 無しの応答には余分キーを足さない（後方互換）
  const plain = parseProfileResponse(OK_PAYLOAD);
  assert.ok(!('src' in plain));
  assert.ok(!('atom' in plain));
});

test('parseProfileResponse passes through bar_width when present, else omitted (後方互換)', () => {
  // Arrange: 応答トップレベルに bar_width（実効レンジpt）がある場合。
  const withBw = {
    ok: true, profile: { bins: [{ price: 100, tpo: 1, norm: 1 }], poc: 100 },
    src: 'candle', atom: '足レンジ', bar_width: 25,
  };
  // Act
  const p = parseProfileResponse(withBw);
  // Assert: bar_width が src/atom と同様に素通しされる。
  assert.equal(p.bar_width, 25);
  assert.equal(p.src, 'candle');
  assert.equal(p.poc, 100);
  // bar_width を含まない応答には bar_width キーを足さない（後方互換）。
  const plain = parseProfileResponse(OK_PAYLOAD);
  assert.ok(!('bar_width' in plain));
});

test('parseProfileResponse includes bar_width even when src/atom absent', () => {
  // Arrange: src/atom が無く bar_width だけある応答でも素通しする。
  const onlyBw = { ok: true, profile: { bins: [], poc: 1 }, bar_width: 12.5 };
  // Act
  const p = parseProfileResponse(onlyBw);
  // Assert
  assert.equal(p.bar_width, 12.5);
});

test('parseProfileResponse returns the profile object on ok:true', () => {
  // Arrange / Act
  const profile = parseProfileResponse(OK_PAYLOAD);
  // Assert
  assert.equal(profile.poc, 101);
  assert.equal(profile.bins.length, 2);
});

test('parseProfileResponse returns null on ok:false', () => {
  // Arrange / Act / Assert
  assert.equal(parseProfileResponse({ ok: false, error: { code: 'x' } }), null);
});

test('parseProfileResponse returns null on malformed payload (no bins array)', () => {
  // Arrange / Act / Assert
  assert.equal(parseProfileResponse({ ok: true, profile: { poc: 1 } }), null);
  assert.equal(parseProfileResponse(null), null);
});

test('MarketProfileClient.fetchProfile builds the URL from context and returns the profile', async () => {
  // Arrange
  const urls = [];
  const fakeFetch = async (u) => { urls.push(u); return { ok: true, async json() { return OK_PAYLOAD; } }; };
  const client = new MarketProfileClient({ fetch: fakeFetch });
  // Act
  const profile = await client.fetchProfile({ datasetRef: 'sample', timeframe: '1D', limit: 1500 });
  // Assert: context に limit が混ざっても URL には limit= を出さない（全期間集計固定）。
  assert.equal(urls.length, 1);
  assert.ok(urls[0].includes('datasetRef=sample') && urls[0].includes('timeframe=1D'));
  assert.ok(!urls[0].includes('limit='), 'limit は URL に付与しない（全期間集計）');
  assert.equal(profile.poc, 101);
});

test('MarketProfileClient.fetchProfile returns null on non-ok HTTP status', async () => {
  // Arrange
  const client = new MarketProfileClient({ fetch: async () => ({ ok: false, status: 500, async json() { return {}; } }) });
  // Act / Assert
  assert.equal(await client.fetchProfile({ datasetRef: 'sample' }), null);
});

test('MarketProfileClient.fetchProfile returns null when fetch throws (non-disruptive)', async () => {
  // Arrange
  const client = new MarketProfileClient({ fetch: async () => { throw new Error('network'); } });
  // Act / Assert
  assert.equal(await client.fetchProfile({ datasetRef: 'sample' }), null);
});

test('MarketProfileClient.fetchProfile returns null when no fetch impl injected', async () => {
  // Arrange
  const client = new MarketProfileClient({ fetch: undefined });
  // Act / Assert
  assert.equal(await client.fetchProfile({ datasetRef: 'sample' }), null);
});


// sessions（日別プロファイル分割）: sessions===true のとき &sessions=1 を付与、false/未指定は付けない。
//   移植元 prototype_260630-01（?sessions=1 で sessions[{date,tpo[]}]）。
test('buildMarketProfileUrl appends &sessions=1 when sessions is true', () => {
  const url = buildMarketProfileUrl({ datasetRef: 'sample', sessions: true });
  assert.ok(url.includes('&sessions=1'));
});

test('buildMarketProfileUrl omits &sessions when false/undefined (sessions OFF・後方互換)', () => {
  assert.ok(!buildMarketProfileUrl({ datasetRef: 'sample' }).includes('sessions='));
  assert.ok(!buildMarketProfileUrl({ datasetRef: 'sample', sessions: false }).includes('sessions='));
});

// parseProfileResponse: トップレベル sessions[] を profile へ素通し（actor が profile.sessions を消費）。
test('parseProfileResponse passes through top-level sessions[] into profile', () => {
  const payload = {
    ok: true,
    profile: {
      bins: [{ price: 100, tpo: 1, norm: 1 }], poc: 100, va_low: 100, va_high: 100,
      price_min: 100, price_max: 101, tpo_units: 1, n_bins: 1,
    },
    sessions: [{ date: '2024-01-01', tpo: [1] }, { date: '2024-01-02', tpo: [2] }],
  };
  const out = parseProfileResponse(payload);
  assert.equal(out.sessions.length, 2);
  assert.equal(out.sessions[0].date, '2024-01-01');
});

test('parseProfileResponse omits sessions key when absent (後方互換)', () => {
  const payload = {
    ok: true,
    profile: {
      bins: [{ price: 100, tpo: 1, norm: 1 }], poc: 100, va_low: 100, va_high: 100,
      price_min: 100, price_max: 101, tpo_units: 1, n_bins: 1,
    },
  };
  const out = parseProfileResponse(payload);
  assert.ok(!('sessions' in out));
});

// sessions_total（キャップ前の実日数・修正1）: primitive 注記「直近N/全M日」の M へ渡す素材。
//   parse はトップレベル sessions_total を profile へ素通しする（無ければ付けない＝後方互換）。
test('parseProfileResponse passes through top-level sessions_total into profile', () => {
  const payload = {
    ok: true,
    profile: {
      bins: [{ price: 100, tpo: 1, norm: 1 }], poc: 100, va_low: 100, va_high: 100,
      price_min: 100, price_max: 101, tpo_units: 1, n_bins: 1,
    },
    sessions: [{ date: '2024-01-01', tpo: [1] }, { date: '2024-01-02', tpo: [2] }],
    sessions_total: 4146, // キャップ前の実日数（キャップ後 len(sessions)=2 とは別）。
  };
  const out = parseProfileResponse(payload);
  assert.equal(out.sessions_total, 4146);
  assert.equal(out.sessions.length, 2);
});

test('parseProfileResponse omits sessions_total when absent (後方互換)', () => {
  const payload = {
    ok: true,
    profile: {
      bins: [{ price: 100, tpo: 1, norm: 1 }], poc: 100, va_low: 100, va_high: 100,
      price_min: 100, price_max: 101, tpo_units: 1, n_bins: 1,
    },
    sessions: [{ date: '2024-01-01', tpo: [1] }],
  };
  const out = parseProfileResponse(payload);
  assert.ok(!('sessions_total' in out));
});
