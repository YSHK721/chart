// tf_period_profile_client.js — GET /tf_period_profile を隔離する取得アダプター（DOM/chart 非依存）。
//
// 「時間足毎のprofile列」機能の front client。ローリング窓 [from, to)（UNIX 秒）ぶんの tf-period 列を取得する。
//   Backend 契約: GET /tf_period_profile?datasetRef=&timeframe=&from=&to=
//     応答 {ok:true, tf, unit, from, to, columns:[{time, levels:[[price,count]...], poc, va_low, va_high,
//            price_min, price_max, tpo_units}]}。失敗時 {ok:false, error} または取得失敗で null。
//   純関数（buildTfPeriodUrl / parseTfPeriod）を公開し単体検証を容易にする（SRP）。市場プロファイルの
//   sparse min-unit 列（実測で短周期も分布成立＝.doc/PROFILE_MICRO_STRUCTURE_VERIFICATION.md）を運ぶ。

export function buildTfPeriodUrl({ datasetRef, timeframe, from, to } = {}) {
  let url = `/tf_period_profile?datasetRef=${encodeURIComponent(datasetRef)}`;
  if (timeframe != null) {
    url += `&timeframe=${encodeURIComponent(timeframe)}`;
  }
  if (from != null) {
    url += `&from=${encodeURIComponent(from)}`;
  }
  if (to != null) {
    url += `&to=${encodeURIComponent(to)}`;
  }
  return url;
}

// 応答 payload を検証して {unit, columns} へ整形する。ok:false・欠損は null（描画非干渉＝非破壊）。
export function parseTfPeriod(payload) {
  if (!payload || payload.ok !== true || !Array.isArray(payload.columns)) {
    return null;
  }
  return {
    tf: payload.tf,
    unit: Number(payload.unit),
    from: payload.from,
    to: payload.to,
    columns: payload.columns,
  };
}

export class TfPeriodProfileClient {
  // fetch はポート注入（テストは Fake、composition root は globalThis.fetch を bind して渡す）。
  constructor({ fetch } = {}) {
    this._fetch = fetch;
  }

  // {datasetRef, timeframe, from, to} から tf-period 列を取得する。失敗・非 ok・例外・整形失敗は null。
  async fetchWindow({ datasetRef, timeframe, from, to } = {}) {
    if (typeof this._fetch !== 'function') {
      return null;
    }
    try {
      const resp = await this._fetch(buildTfPeriodUrl({ datasetRef, timeframe, from, to }));
      if (!resp.ok) {
        return null;
      }
      return parseTfPeriod(await resp.json());
    } catch {
      return null;
    }
  }
}
