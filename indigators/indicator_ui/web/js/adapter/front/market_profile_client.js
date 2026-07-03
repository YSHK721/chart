// market_profile_client.js — GET /market_profile を隔離する取得アダプター（DOM/chart 非依存）。
//
// 設計入力: 依頼「サーバ呼び出しの作法（既存 fetch/usecase 層）に合わせる」。
//   composition_root_front.js の fetchCandles / fetchFormingBar と同型（URL 組み立て・fetch・失敗時 null）。
//   Backend 契約: GET /market_profile?datasetRef=&timeframe=&limit=&bins=&va=
//     応答 {ok:true, profile:{bins:[{price,tpo,norm}],poc,va_low,va_high,price_min,price_max,tpo_units,n_bins}}
//     失敗時 {ok:false, error:{...}}。
//   純関数（buildMarketProfileUrl / parseProfileResponse）を公開し単体検証を容易にする（SRP）。

// datasetRef を必須、timeframe/bins/va/src/range は与えられた場合のみ付加する（省略時はサーバ既定）。
//   limit は受理しない＝MP は常に全期間集計（backend は limit 省略時＝全件集計）。
//   src: 集計原子（'candle'=足レンジ・既定 / 'dwell'=実ティック滞在 / 'm1'=tick数）。省略時は付与せず candle 後方互換。
//   resmode: 解像度モード（'bins'=ビン / 'range'=レンジ）。試作 prototype_260630-01 の解像度トグル移植。
//     resmode==='range' のとき range（レンジpt）を backend param 名 barw へ写像し bins は送らない。
//     それ以外（'bins' / 未指定）は bins を送り barw は送らない。bins は ENUM プリセット化で文字列
//     （'30'/'60'/'100'）が渡るため、文字列プリセット（非空）または有限数（後方互換）を付与し、
//     NaN・空文字・null は排除する（backend の _parse_int が '60' を解釈可）。
export function buildMarketProfileUrl({
  datasetRef, timeframe, bins, va, src, range, resmode, to, from, today, sessions, day,
} = {}) {
  let url = `/market_profile?datasetRef=${encodeURIComponent(datasetRef)}`;
  if (timeframe != null) {
    url += `&timeframe=${encodeURIComponent(timeframe)}`;
  }
  // limit は付与しない＝MP は常に全期間集計（backend が limit 省略時＝全件集計）。
  //   context に limit が混ざっても（getContext の recentBars 等）URL には出さない。
  // 解像度モードで bins / barw の送信を排他化する。
  if (resmode === 'range') {
    // レンジ指定 → barw のみ送る（bins は送らない）。'auto'/null は付与しない防御。
    if (range != null && range !== 'auto') {
      url += `&barw=${encodeURIComponent(range)}`;
    }
  } else if (Number.isFinite(bins) || (typeof bins === 'string' && bins !== '')) {
    // ビン指定（既定）→ ENUM 文字列プリセット（非空）または有限数（後方互換）のときのみ bins を付与。
    //   NaN（貼付等で数値化に失敗）・空文字・null は無効な &bins= を送出しない。
    url += `&bins=${encodeURIComponent(bins)}`;
  }
  if (va != null) {
    url += `&va=${encodeURIComponent(va)}`;
  }
  if (src != null) {
    url += `&src=${encodeURIComponent(src)}`;
  }
  // to（リプレイ時間カーソル・UNIX 秒）— 指定時のみ付与（省略時=全期間＝現行挙動・後方互換）。
  //   移植元 prototype_260630-01（as-seen-at-t・アンカー）。backend が time<=to の足だけで集計する。
  if (to != null) {
    url += `&to=${encodeURIComponent(to)}`;
  }
  // from（ローリング窓の下限 time・UNIX 秒）— 指定時のみ付与（省略時=全期間・後方互換）。増分2 A。
  //   移植元 prototype_260630-01（ローリング窓 = T-ROLL_BARS本）。to と併用で [from,to] のローリング窓。
  if (from != null) {
    url += `&from=${encodeURIComponent(from)}`;
  }
  // today（スナップショット当日強調）— true のときのみ &today=1 を付与（false/未指定は付けない・後方互換）。
  //   移植元 prototype_260630-01（?today=1 で today[]/today_max）。増分2 C。
  if (today === true || today === 1 || today === '1') {
    url += '&today=1';
  }
  // sessions（日別プロファイル分割）— true のときのみ &sessions=1 を付与（false/未指定は付けない・後方互換）。
  //   移植元 prototype_260630-01（?sessions=1 で sessions[{date,tpo[]}]）。
  if (sessions === true || sessions === 1 || sessions === '1') {
    url += '&sessions=1';
  }
  // day（単日拡大ビューの左70%パス）— 指定時のみ &day=<YYYY-MM-DD> を付与（省略/null は付けない・後方互換）。
  //   本タスク: 左70%=その日のティック推移。backend が tick 対応 ref のとき day_path を応答へ付加する。
  if (day != null && day !== '') {
    url += `&day=${encodeURIComponent(day)}`;
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
  // 応答トップレベルに src/atom/bar_width（UI メタ）・sessions（日別分割）があれば profile へ素通しする
  // （無ければ既存 profile をそのまま返す＝後方互換・共有オブジェクト非破壊の spread 維持）。
  if (payload.src != null || payload.atom != null || payload.bar_width != null
      || payload.sessions != null || payload.sessions_total != null || payload.day_path != null) {
    const meta = {};
    if (payload.src != null) meta.src = payload.src;
    if (payload.atom != null) meta.atom = payload.atom;
    if (payload.bar_width != null) meta.bar_width = payload.bar_width;
    // sessions[{date,tpo[]}]（日別プロファイル分割）— actor が profile.sessions を primitive へ渡す。
    if (payload.sessions != null) meta.sessions = payload.sessions;
    // sessions_total（キャップ前の実日数）— primitive 注記「直近N/全M日」の M（キャップ後 60 の誤読防止）。
    if (payload.sessions_total != null) meta.sessions_total = payload.sessions_total;
    // day_path[{t,p}]（単日ティック推移）— actor が単日拡大時に primitive の左70%パスへ渡す。
    if (payload.day_path != null) meta.day_path = payload.day_path;
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
