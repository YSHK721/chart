// market_profile_client.js — GET /market_profile を隔離する取得アダプター（DOM/chart 非依存）。
//
// 設計入力: 依頼「サーバ呼び出しの作法（既存 fetch/usecase 層）に合わせる」。
//   composition_root_front.js の fetchCandles / fetchFormingBar と同型（URL 組み立て・fetch・失敗時 null）。
//   Backend 契約: GET /market_profile?datasetRef=&timeframe=&limit=&bins=&va=
//     応答 {ok:true, profile:{bins:[{price,tpo,norm}],poc,va_low,va_high,price_min,price_max,tpo_units,n_bins}}
//     失敗時 {ok:false, error:{...}}。
//   純関数（buildMarketProfileUrl / parseProfileResponse）を公開し単体検証を容易にする（SRP）。

// datasetRef を必須、timeframe/limit/bins/va/src は与えられた場合のみ付加する（省略時はサーバ既定）。
//   src: 集計原子（'candle'=足レンジ・既定 / 'dwell'=実ティック滞在）。省略時は付与せず candle 後方互換。
export function buildMarketProfileUrl({ datasetRef, timeframe, limit, bins, va, src } = {}) {
  let url = `/market_profile?datasetRef=${encodeURIComponent(datasetRef)}`;
  if (timeframe != null) {
    url += `&timeframe=${encodeURIComponent(timeframe)}`;
  }
  if (limit != null) {
    url += `&limit=${encodeURIComponent(limit)}`;
  }
  if (bins != null) {
    url += `&bins=${encodeURIComponent(bins)}`;
  }
  if (va != null) {
    url += `&va=${encodeURIComponent(va)}`;
  }
  if (src != null) {
    url += `&src=${encodeURIComponent(src)}`;
  }
  return url;
}

// 応答 payload を primitive が消費できる profile へ整形する。ok:false・欠損・bins 非配列は null。
export function parseProfileResponse(payload) {
  if (!payload || payload.ok !== true) {
    return null;
  }
  const profile = payload.profile;
  if (!profile || !Array.isArray(profile.bins)) {
    return null;
  }
  // 応答トップレベルに src/atom（UI メタ）があれば profile へ素通しする（無ければ既存 profile をそのまま返す）。
  if (payload.src != null || payload.atom != null) {
    const meta = {};
    if (payload.src != null) meta.src = payload.src;
    if (payload.atom != null) meta.atom = payload.atom;
    return { ...profile, ...meta };
  }
  return profile;
}

export class MarketProfileClient {
  // fetch はポート注入（テストは Fake、composition root は globalThis.fetch を bind して渡す）。
  constructor({ fetch } = {}) {
    this._fetch = fetch;
  }

  // context（datasetRef/timeframe/limit/bins/va）から profile を取得する。
  //   fetch 不在・HTTP 非 ok・例外・整形失敗はすべて null（既存 candles 描画へ非干渉＝非破壊）。
  async fetchProfile(context = {}) {
    if (typeof this._fetch !== 'function') {
      return null;
    }
    try {
      const resp = await this._fetch(buildMarketProfileUrl(context));
      if (!resp.ok) {
        return null;
      }
      return parseProfileResponse(await resp.json());
    } catch {
      return null;
    }
  }
}
