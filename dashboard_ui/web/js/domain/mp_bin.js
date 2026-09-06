// mp_bin（domain/mp_bin.js）— 借用した MP プロファイルの「価格 → bin の密度」写像。
//
// 設計入力（依頼者承認 2026-09-06「MP 列＝ライブ MP の借用」・裁定 5/6）:
//   ラダーの MP 列は live core の `/market_profile` 応答をそのまま引く。応答が運ぶのは bin の
//   **配列**なので、行の価格がどの bin に入るかを借り手側で決める必要がある。
//
// なぜ domain に置くか（裁定 6）: これは版面の都合を一切含まない純粋な規則である。View に
//   置くと「描く」と「どの bin か決める」が同じ場所に同居し、規則を単体で検証できなくなる。
//   View へは `mpNormOf(price)` の形で注入する（periodAnnotator と同型・View は受けた値を描くだけ）。
//
// なぜ zp 式でなければならないか（実測 2026-09-06）:
//   借用する既定ソースは zp（`MP_DEFAULT_SOURCE`）であり、その bin 帰属の参照実装は
//   `market_profile_api/compute/market_profile_zp.py:400,441,458`:
//       binw  = (price_max - price_min) / n_bins
//       index = clip(int((price - price_min) / binw), 0, n_bins - 1)
//   一方 dashboard のサーバ側 `_norm_at`（market_profile_gateway.py:239）は
//       index = min(n_bins - 1, int((price - price_min) / span * n_bins))
//   を使う。これは **candle 版 MP core** に合わせた式であり、zp とは丸めの回数が 1 回違う。
//   代数的には同値でも浮動小数では別物で、price_min=38000 / price_max=42000 / n_bins=60 の
//   bin 境界 61 点のうち 6 点（39000 / 39800 / 40000 / 41400 / 41600 / 41800）で別の bin へ
//   落ちる。**借用元が zp である以上、合わせる相手は zp の式である**。
//   ずれても「隣の bin の濃さ」が出るだけで版面は正しく見えるため、状態検証では原理的に
//   落ちない種類のずれである。式の選択そのものを tests/mp_bin.test.js が固定する。
//
// 計算量: 1 価格あたり定数時間（bin の走査をしない）。行数が増えても profile は 1 本のまま
//   ＝行数に比例するのは引き算だけで、取得も再計算も発行しない（絶対命令 §4.1）。

/**
 * 価格が入る bin の密度 `norm`。プロファイルの価格域の外・素材なしは null。
 *
 * 範囲外を端の bin へ丸めない理由: zp の compute が clip するのは**集計側**の規則
 * （fine 格子を表示 bin へ畳む都合）であって、引く側の規則ではない。版面で丸めると
 * 「プロファイルの外にある水準」が端の濃さで塗られ、外であることが読めなくなる。
 * 密度が無い行はバーを描かない（0 幅・無色のバーは「密度が最小」と誤読される）。
 *
 * @param {?object} profile `/market_profile` の profile（bins / n_bins / price_min / price_max）
 * @param {number}  price   行の価格
 * @returns {?number} 0..1 の密度、または null
 */
export function mpNormAt(profile, price) {
  if (!profile) {
    return null;
  }
  const bins = Array.isArray(profile.bins) ? profile.bins : null;
  if (!bins || bins.length === 0) {
    return null;
  }
  const nBins = Number(profile.n_bins);
  if (!Number.isFinite(nBins) || nBins <= 0) {
    return null;
  }
  const at = Number(price);
  if (!Number.isFinite(at)) {
    return null;
  }
  const priceMin = Number(profile.price_min);
  const priceMax = Number(profile.price_max);
  if (!Number.isFinite(priceMin) || !Number.isFinite(priceMax)) {
    return null;
  }
  // 参照実装（market_profile_zp.py:400）と同じく**幅を先に出す**。ここを
  //   `(price - price_min) / span * n_bins` に書き換えると bin 境界で zp とずれる（上記の 6 点）。
  const binw = (priceMax - priceMin) / nBins;
  if (!(binw > 0)) {
    return null;   // 素材 1 点（span 0）。幅 0 で割ると添字が Infinity になる。
  }
  if (at < priceMin || at > priceMax) {
    return null;
  }
  // 上端（price === price_max）は最後の bin に属する（区間の閉じ側を落とさない）。
  //   Math.trunc は numpy の `.astype(int)`（0 方向への切り捨て）と同じ丸めである。
  const index = Math.min(nBins - 1, Math.trunc((at - priceMin) / binw));
  if (index < 0 || index >= bins.length) {
    return null;
  }
  const bin = bins[index];
  // `null` を Number へ通すと 0 になる。「密度なし」を「密度 0」へ化かすと、版面には
  //   最も薄いバーが出て、素材が無いことが読めなくなる。先に不在を弾く。
  const raw = bin ? bin.norm : null;
  if (raw === null || raw === undefined) {
    return null;
  }
  const norm = Number(raw);
  return Number.isFinite(norm) ? norm : null;
}
