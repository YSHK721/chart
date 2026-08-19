// 投入契約（純関数・Phase 9 S2 M5）。
//
// 役割: 「実行対象データセット（profile）」「実行対象（subject＝EA と口座）」「EA パラメータ
//   （inputs）」の 3 つから `POST /sim/jobs` の本文を組む。あわせて、実行対象データセット
//   一覧から実行対象を引く規則（`resolveProfile` / `symbolCandidatesOf`）を所有する。
//
// なぜ純関数か: 本文の組み立て規則は front の中で最も壊れやすく、最も検証したい箇所である
//   （キーの取りこぼし・型の落ち方は実測でしか分からない）。DOM や HTTP と同じ面に置くと
//   規則を確かめるだけで器と通信のダブルが要る。ここは依存 0 で保ち、node:test から素で
//   呼べる状態にする（構造ガードは tests/import_source.test.js が機械強制する）。
//
// front リテラル 0 の原則: 銘柄仕様（contract_size・point_size …）の値をここへ書かない。
//   下の PROFILE_KEYS が持つのは**キー名だけ**であり、値は注入 profile からのみ来る。

/** profile 由来の 11 キー（build_interactor の銘柄仕様・data_path/symbol/period）。 */
export const PROFILE_KEYS = Object.freeze([
  "data_path", "symbol", "period", "contract_size", "digits", "point_size",
  "leverage", "volume_min", "volume_max", "volume_step", "stops_level",
]);

/**
 * 実行対象データセットを Symbol から決める。
 *
 * 決定的であることが要点である: 同じ (datasets, symbol) からは必ず同じ profile が出る。
 * symbol 一致の**先頭**を採り、一致が無ければ null を返す（既定へ当てはめない——
 * 当てはめると「選んでいない銘柄で回った」ことが画面から分からなくなる）。
 *
 * @param {object[]} datasets GET /sim/run-options の datasets
 * @param {string}   symbol   利用者が選んだ Symbol
 * @returns {object|null}
 */
export function resolveProfile(datasets, symbol) {
  if (!Array.isArray(datasets) || !datasets.length) return null;
  const wanted = symbol === null || symbol === undefined ? "" : String(symbol);
  if (wanted === "") return null;
  return datasets.find((p) => p && String(p.symbol) === wanted) || null;
}

/**
 * 選べる銘柄の一覧を実行対象データセットから引く（resolveProfile と対の規則）。
 *
 * 同じ銘柄のデータセットが複数あっても候補は 1 つに畳む——候補は「選べる銘柄」であって
 * 「データセットの数」ではない。並びは datasets の出現順であり、同じ入力からは必ず同じ
 * 一覧が出る（決定的）。値は select の値になるため常に文字列で返す。
 *
 * 合成根ではなくここに置く理由: これは結線ではなく規則である。合成根に書くと、規則を
 * 確かめるだけで器と通信のダブルが要る（M5 が投入契約を引き受けているのと同じ理由）。
 *
 * @param {object[]} datasets GET /sim/run-options の datasets
 * @returns {string[]}
 */
export function symbolCandidatesOf(datasets) {
  if (!Array.isArray(datasets)) return [];
  return [...new Set(datasets.map((d) => String(d.symbol)))];
}

/**
 * 投入本文を組む。
 *
 * `strategy`（買い/売り条件・建玉変更）は**常に不在**である。機能は API 面で存続するが、
 * front からの出口は Phase 9 S1 で撤去した（§19.2）。
 *
 * `settings` は null なら本文へ載せない。サーバは body.get("settings") を読むため、
 * キー不在は「設定ブロック無し」と等価であり、縮退面からの投入は旧本文と byte 等価になる。
 *
 * @param {object|null} profile 実行対象データセット（resolveProfile の結果）
 * @param {object} subject      {ea_name, initial_deposit, settings} — 実行対象の供給元
 * @param {object} inputs       EA パラメータ（宣言表 param → 変換済み値）
 * @returns {object} `POST /sim/jobs` の本文
 */
export function buildSubmission({ profile, subject, inputs } = {}) {
  const source = subject || {};
  const backtest = {
    ea_name: String(source.ea_name || ""),
    initial_deposit: Number(source.initial_deposit),
    ...(inputs || {}),
  };
  if (profile) {
    for (const key of PROFILE_KEYS) backtest[key] = profile[key];
    // profile が config_overrides（例 entry_price_basis）を持てば素通しする（front リテラル 0）。
    // データセットの CSV 形式・EA ローダの組合せで既定の建値基準が成立しない場合に profile が
    // 権威供給する（config_overrides は E-5b の任意キー＝build_interactor の同名 param）。
    if (profile.config_overrides) backtest.config_overrides = profile.config_overrides;
  }
  const body = { backtest };
  const settings = source.settings;
  if (settings !== null && settings !== undefined) body.settings = settings;
  return body;
}
