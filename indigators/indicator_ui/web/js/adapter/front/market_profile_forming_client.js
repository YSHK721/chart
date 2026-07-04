// market_profile_forming_client.js — GET /market_profile_forming を隔離する取得アダプター（DOM/chart 非依存）。
//
// 設計入力: Phase2 設計 mp_ticklive_design.md「新規 front client（Port 実装・DIP）」。
//   market_profile_client.js（fetchProfile）と同型（URL 組み立て・fetch・失敗時 null）。
//   Backend 契約: GET /market_profile_forming?datasetRef=&timeframe=&since=&base=[&bins=&va=&barw=]
//     応答 {ok:true, formingStart, ticks:[[sec,mid]...], now[, baseFine, baseKmin, activeTable,
//            priceMin, priceMax, nBins, gridW]}。baseFine は GRID_W 固定グリッド（表示 bin 再集計前・
//            忠実 binning 用）。失敗時 {ok:false, error:{...}}。
//   純関数（buildFormingUrl / parseForming）を公開し単体検証を容易にする（SRP）。

// datasetRef を必須、timeframe/since/base/now と bins|barw（resmode で排他）を与えられた場合のみ付加する。
//   base=1（既定 full・base+activeTable 同梱）/ base=0（軽量・forming tick 尾部のみ）。
//   resmode==='range' のとき range（レンジpt）を backend param barw へ写像し bins は送らない（base の
//   表示 bin を live 表示と整列させる。market_profile_client.buildMarketProfileUrl と同一規則）。
export function buildFormingUrl({
  datasetRef, timeframe, since, base, now, bins, va, range, resmode,
} = {}) {
  let url = `/market_profile_forming?datasetRef=${encodeURIComponent(datasetRef)}`;
  if (timeframe != null) {
    url += `&timeframe=${encodeURIComponent(timeframe)}`;
  }
  if (since != null) {
    url += `&since=${encodeURIComponent(since)}`;
  }
  if (base != null) {
    url += `&base=${encodeURIComponent(base)}`;
  }
  if (now != null) {
    url += `&now=${encodeURIComponent(now)}`;
  }
  // 解像度モードで bins / barw の送信を排他化する（base 表示 bin の整列・buildMarketProfileUrl と同型）。
  if (resmode === 'range') {
    if (range != null && range !== 'auto') {
      url += `&barw=${encodeURIComponent(range)}`;
    }
  } else if (Number.isFinite(bins) || (typeof bins === 'string' && bins !== '')) {
    url += `&bins=${encodeURIComponent(bins)}`;
  }
  if (va != null) {
    url += `&va=${encodeURIComponent(va)}`;
  }
  return url;
}

// 応答 payload を検証して素通しする。ok:true かつ formingStart（数）と ticks（配列）を持つときのみ
//   payload を返す。ok:false・欠損・型不一致は null（既存描画へ非干渉＝非破壊）。
export function parseForming(payload) {
  if (!payload || payload.ok !== true) {
    return null;
  }
  if (typeof payload.formingStart !== 'number' || !Array.isArray(payload.ticks)) {
    return null;
  }
  return payload;
}

export class MarketProfileFormingClient {
  // fetch はポート注入（テストは Fake、composition root は globalThis.fetch を bind して渡す）。
  constructor({ fetch } = {}) {
    this._fetch = fetch;
  }

  // args（datasetRef/timeframe/since/base/…）から forming payload を取得する。
  //   fetch 不在・HTTP 非 ok・例外・整形失敗はすべて null（既存描画へ非干渉＝非破壊）。
  async fetchForming(args = {}) {
    if (typeof this._fetch !== 'function') {
      return null;
    }
    try {
      const resp = await this._fetch(buildFormingUrl(args));
      if (!resp.ok) {
        return null;
      }
      return parseForming(await resp.json());
    } catch {
      return null;
    }
  }
}
