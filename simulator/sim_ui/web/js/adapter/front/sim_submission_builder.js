// 投入契約（純関数・Phase 9 S2 M5）。
//
// 役割: 「実行対象データセット（profile）」「実行対象（subject＝EA と口座）」「EA パラメータ
//   （inputs）」の 3 つから `POST /sim/jobs` の本文を組む。あわせて、実行対象データセット
//   一覧から実行対象を引く規則（`resolveProfile` / `symbolCandidatesOf` /
//   `seriesCandidatesOf`）を所有する。
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

/** 系列の軸を出すのに要る候補数（ISSUE-511 段階 8-D-5）。
 *
 *  1 本しか無い銘柄では軸を出さない——実在しない分岐を画面に出すと、選ぶものが 1 つしか
 *  無い操作を利用者に読ませることになる（認知負荷の最小化）。この閾値は**規則**であって
 *  器の都合ではないため、判定はここが持つ（View は銘柄候補と同じく「候補が在れば出す」
 *  だけを見る＝しきい値の第 2 実装を作らない）。 */
const SERIES_AXIS_MIN_CANDIDATES = 2;

/** 空文字へ畳んだトークン（未指定・null・undefined を 1 つの形にする）。 */
const tokenOf = (raw) => (raw === null || raw === undefined ? "" : String(raw));

/**
 * 2 つの候補列が同じか（＝配り直しても画面の出力は 1 ビットも変わらないか）。
 *
 * 判定がここに在る理由: これは「同じものを配られたら組み直さない」という**規則**であり、
 * 両方の供給元（M1 Tester Settings 面 / M4 縮退面）が同じ答えを出さなければならない。
 * 面ごとに手書きすると、片方だけが作り直す状態が静かに生まれる（出力は正しいままなので
 * 状態検証では落ちない）。規則は 1 箇所に置き、面は呼ぶだけにする。
 *
 * @param {string[]} a いま画面に出している候補列
 * @param {string[]} b これから配る候補列
 * @returns {boolean}
 */
export function sameCandidates(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
  return a.every((token, i) => token === b[i]);
}

/**
 * 実行対象データセットを Symbol（＋任意で系列）から決める。
 *
 * 決定的であることが要点である: 同じ (datasets, symbol, series) からは必ず同じ profile が
 * 出る。系列の指定が**無ければ** symbol 一致の先頭を採る（段階 8-D-5 以前と同一の決め方＝
 * 軸を出さない構成の投入本文は 1 バイトも変わらない）。系列の指定が**在れば**その系列を
 * 探す——先頭決め打ちにすると、画面で 2 本目を選んでも 1 本目で回る（選んだことが結果に
 * 現れない沈黙の失敗）。
 *
 * 一致が無ければ null を返す（既定へ当てはめない——当てはめると「選んでいない銘柄・系列で
 * 回った」ことが画面から分からなくなる）。系列は**銘柄の内側**の軸なので、別銘柄の系列を
 * 指定しても銘柄を乗り換えない。
 *
 * @param {object[]} datasets GET /sim/run-options の datasets
 * @param {string}   symbol   利用者が選んだ Symbol
 * @param {string}   [series] 利用者が選んだ系列（`RunProfile.dataset`＝ref 名）
 * @returns {object|null}
 */
export function resolveProfile(datasets, symbol, series) {
  if (!Array.isArray(datasets) || !datasets.length) return null;
  const wanted = tokenOf(symbol);
  if (wanted === "") return null;
  const ref = tokenOf(series);
  // 1 回の走査で決める（候補の中間配列を作って捨てない）。
  for (const profile of datasets) {
    if (!profile || String(profile.symbol) !== wanted) continue;
    if (ref === "") return profile;                        // 系列の指定なし＝一致の先頭
    if (String(profile.dataset) === ref) return profile;   // 指定された系列
  }
  return null;
}

/**
 * 選べる系列の一覧を実行対象データセットから引く（resolveProfile と対の規則）。
 *
 * `symbolCandidatesOf` は同じ銘柄のデータセットを 1 候補へ畳む（候補は「選べる銘柄」で
 * あって「データセットの数」ではない）。**畳んだ先を選び直す第 2 の軸**がこれである。
 * 識別子は `RunProfile.dataset`（ref 名）であり、投入本文の `PROFILE_KEYS` に含まれない
 * ——系列を選んでも本文のキーは 1 つも増えない（増えるのは値の出所だけ）。
 *
 * 並びは datasets の出現順であり、同じ入力からは必ず同じ一覧が出る（決定的）。値は select
 * の値になるため常に文字列で返す。ラベルも同じ ref 名である（`RunProfile.dataset` が
 * 「セレクタのラベル/値」の権威＝front は表示名を作らない）。
 *
 * **分岐が実在しないときは空を返す**（SERIES_AXIS_MIN_CANDIDATES）。空は View にとって
 * 銘柄候補 0 件と同じ意味であり、軸は画面に出ない＝現行画面と同一になる。
 *
 * @param {object[]} datasets GET /sim/run-options の datasets
 * @param {string}   symbol   利用者が選んだ Symbol
 * @returns {string[]}
 */
export function seriesCandidatesOf(datasets, symbol) {
  if (!Array.isArray(datasets)) return [];
  const wanted = tokenOf(symbol);
  const refs = [];
  for (const profile of datasets) {
    if (!profile || String(profile.symbol) !== wanted) continue;
    refs.push(String(profile.dataset));
  }
  // 足りなければ**同じ配列を空にして**返す（2 本目を作らない＝捨てる生成を持たない）。
  if (refs.length < SERIES_AXIS_MIN_CANDIDATES) refs.length = 0;
  return refs;
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
 * 日付トークン → epoch 秒（**UTC 解釈**・ISSUE-508 段階 3 §6.4）。
 *
 * なぜ front の責務か: サーバは `trace.start` / `trace.end` を **epoch 秒の整数**で受ける。
 *   そうすると (a) 新しい parser を書かない (b) `EPOCH_CONVERTERS` を広げない
 *   (c) 第 2 の日付規則を作らない (d) 受付境界での変換そのものが要らなくなる、が同時に
 *   満たされる。人が日付で入力する変換だけが残り、それはここにしか無い。
 *
 * なぜ UTC 解釈か: ローカル TZ で解釈すると、同じ入力でも実行環境で記録される期間が
 *   変わる（ISSUE-401 で `TZ=Asia/Tokyo` の 32,400 秒差を実測済み）。`Date` の
 *   ローカル構築子（`new Date(y, m, d)`）を使わず、明示的に `Z` を付けて解釈する。
 *
 * front の日付表記は 1 つに保つ（§6.4「第 2 の日付規則を作らない」）: 画面に既に在る
 *   日付入力は `YYYY.MM.DD`（カレンダー `sim_date_picker_view` が確定するトークン形）を
 *   使っている。トレースの期間欄だけ別表記にすると、同じ「日付」に画面上の呼び名が 2 つ
 *   できる。**解釈規則を増やすのではなく**、この 1 関数が点区切りも受ける（規則は 1 つ）。
 *
 * @param {string} token `YYYY.MM.DD` / `YYYY-MM-DD` / `YYYY-MM-DDThh:mm[:ss]`
 * @returns {number} epoch 秒（整数）
 */
export function epochSecondsOfDate(rawToken) {
  if (typeof rawToken !== "string" || rawToken === "") {
    throw new Error(`trace の期間として解釈できません: ${JSON.stringify(rawToken)}`);
  }
  // 点区切りの日付トークンは ISO の日付部と同じものを指す（解釈は下の 1 経路だけ）。
  const token = /^[0-9]{4}\.[0-9]{2}\.[0-9]{2}$/.test(rawToken)
    ? rawToken.replace(/\./g, "-")
    : rawToken;
  // 存在しない日を**黙って**繰り上げない。`Date.parse` は `2024-02-30T00:00:00Z` を
  // 2024-03-01 として受理する（実測 2026-09-10）。打った日と違う日が記録される形であり、
  // §6.4 が禁じた「同じ文字列が経路で違う時刻に化ける」そのものである。繰り上げは front で
  // 完結するため、受け取る側は epoch 整数として矛盾が無く**原理的に検出できない**。
  // 暦としての実在は日付部だけで決まるので、時刻部・タイムゾーン指定の有無に関わらず見る。
  const ymd = /^([0-9]{4})-([0-9]{2})-([0-9]{2})/.exec(token);
  if (ymd) {
    const [year, month, day] = [Number(ymd[1]), Number(ymd[2]), Number(ymd[3])];
    const probe = new Date(Date.UTC(year, month - 1, day));
    if (probe.getUTCFullYear() !== year
      || probe.getUTCMonth() + 1 !== month
      || probe.getUTCDate() !== day) {
      throw new Error(`trace の期間に実在しない日付があります: ${JSON.stringify(rawToken)}`);
    }
  }
  // 時刻部が無ければ UTC の 0 時。時刻部が在ってもタイムゾーン指定が無ければ UTC。
  const hasTime = token.includes("T");
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(token);
  const normalised = hasZone ? token : `${hasTime ? token : `${token}T00:00:00`}Z`;
  const ms = Date.parse(normalised);
  if (!Number.isFinite(ms)) {
    // 理由は**打たれたまま**を示す（正規化後の姿を出すと、画面の入力と食い違う）。
    throw new Error(`trace の期間として解釈できません: ${JSON.stringify(rawToken)}`);
  }
  return Math.floor(ms / 1000);
}

/** 1 日（秒）。UTC の 0 時に足せば必ず翌日の 0 時になる（UTC に夏時間は無い）。 */
const SECONDS_PER_DAY = 24 * 60 * 60;

/**
 * 実行トレースの投入ブロックを組む（ISSUE-508 段階 3 §6.6.2）。
 *
 * 記録の既定は**明示 ON ＋期間指定**（依頼者裁定）なので、OFF は `null` を返して
 * 本文へ載せない（既存投入と byte 等価）。
 *
 * 期間の妥当性はここでも落とす: サーバまで運んで 400 を貰うより、押した瞬間に理由が
 * 分かる方が速い。**規則そのものを増やしているのではない**——サーバ側の `TraceWindow`
 * が唯一の権威であり、ここは同じ誤りを早く見つけるためだけの前置きである
 * （通ってしまった場合でもサーバが必ず落とす＝二重の安全側であって二重定義ではない）。
 *
 * **終了日はその日を含む**（裁定 2026-09-10）。半開区間の上端は翌日 0 時になる。
 * これは本リポジトリで既に確定している「終了日」の意味と同じである——`.ini` の
 * `FromDate` / `ToDate` を解決する唯一の場所 `main/tester_settings/window.py:155-156` が
 * `start = _midnight_utc(from_date)` / `end = _midnight_utc(to_date) + timedelta(days=1)`
 * であり、docstring も `[from 00:00Z, to+1day 00:00Z)` と明記する。トレース面だけ
 * 「含まない」にすると、同じ画面の同じ語「終了日」が 2 つの意味を持つことになる。
 *
 * 変換の実体は増やしていない: `epochSecondsOfDate` の意味（日付トークン → その日の 0 時）
 * はそのままで、+1 日は**呼出側であるここ**が足す（`window.py` も `_midnight_utc` を
 * 素のまま使い、`+ timedelta(days=1)` を呼出側に置いている。同じ形である）。
 *
 * @param {object|null} trace {enabled, from, to}（from/to は日付トークン）
 * @returns {object|null} `{enabled: true[, start, end]}` または null
 */
export function traceBlockOf(trace) {
  if (!trace || !trace.enabled) return null;
  const { from, to } = trace;
  const hasFrom = from !== null && from !== undefined && from !== "";
  const hasTo = to !== null && to !== undefined && to !== "";
  if (!hasFrom && !hasTo) return { enabled: true };
  if (hasFrom !== hasTo) {
    throw new Error(
      "trace の期間は開始と終了の両方を指定してください"
      + "（片側だけでは残す範囲が決まりません）",
    );
  }
  // 打たれた日付そのもの（＝その日の 0 時）。上端の +1 日はこの後で足す。
  const startOfFromDay = epochSecondsOfDate(from);
  const startOfToDay = epochSecondsOfDate(to);
  // 逆転の判定は**利用者が打った日付同士**で行う。上端に +1 日を足した後の値で比べると、
  // 1 日だけ逆転した期間（開始が終了の翌日）が start === end になってすり抜ける
  // （実測 2026-09-10: 素朴実装で `2025.01.11 → 2025.01.10` が通った）。
  if (startOfFromDay > startOfToDay) {
    throw new Error(`trace の期間が逆転しています: ${from} → ${to}`);
  }
  return { enabled: true, start: startOfFromDay, end: startOfToDay + SECONDS_PER_DAY };
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
 * `trace` も同じ形（不在・OFF なら載せない）。日付 → epoch 秒の変換は
 * `traceBlockOf` が担う（§6.4: 変換は front の責務）。
 *
 * @param {object|null} profile 実行対象データセット（resolveProfile の結果）
 * @param {object} subject      {ea_name, initial_deposit, settings} — 実行対象の供給元
 * @param {object} inputs       EA パラメータ（宣言表 param → 変換済み値）
 * @param {object|null} trace   {enabled, from, to} — 実行トレースの指定
 * @returns {object} `POST /sim/jobs` の本文
 */
export function buildSubmission({ profile, subject, inputs, trace } = {}) {
  const source = subject || {};
  const backtest = {
    ea_name: String(source.ea_name || ""),
    initial_deposit: Number(source.initial_deposit),
    ...(inputs || {}),
  };
  if (profile) {
    for (const key of PROFILE_KEYS) backtest[key] = profile[key];
    // profile が config_overrides（例 tick_model）を持てば素通しする（front リテラル 0）。
    // 値の権威はカタログ側であり front は中身を解釈しない（config_overrides は E-5b の
    // 任意キー＝build_interactor の同名 param）。建値基準はここを通らない——値の出所は
    // 戦略の宣言ただ 1 つであり、設定へ書けば実行段が拒む（ISSUE-533 段階 2）。
    if (profile.config_overrides) backtest.config_overrides = profile.config_overrides;
  }
  const body = { backtest };
  const settings = source.settings;
  if (settings !== null && settings !== undefined) body.settings = settings;
  const traceBlock = traceBlockOf(trace);
  if (traceBlock !== null) body.trace = traceBlock;
  return body;
}
