// mp_fetch_context（adapter/front/mp_fetch_context.js）
//   — テンプレートの MP 設定を、ライブと**同一の** fetch 文脈へ写す。
//
// 設計入力（依頼者承認 2026-09-06「MP 列＝ライブ MP の借用」・裁定 3）:
//   借用が意味を持つのは、ラダーが投げる文脈がライブチャートのそれと一致するときだけである。
//   ずれた瞬間にサーバ側メモの共有も仕様の同期も失われ、「似ているが別の MP」が版面に出る
//   ——出力はもっともらしいままなので、状態検証では原理的に落ちない。
//
// したがって写像は**自前で書かない**。ライブの `MpFetchParams`（mp_fetch_params.js:29）を
//   注入で受け取り、その host 契約（同ファイル 14-17 行の宣言）を満たす最小のアダプタを
//   ここに置く:
//       field : _sessions（日別モードか）/ _getCandles / _nowSec
//       method: _replayScrub.isReplay()
//   受理キーの白表（ACCEPTED_KEYS）も、period 窓の条件も、dispbp→barw 写像も、ライブが
//   変われば自動で追随する。ACCEPTED_KEYS を素通しするだけの写しを作ると、host を要求する
//   3 つの extra（sessionsExtra / periodExtra / dispExtra）が丸ごと落ちる。
//
// ダッシュボードは常にライブである（リプレイ経路を持たない）:
//   - `_replayScrub.isReplay()` は常に false。
//   - clockExtra 相当は載せない。ライブ present の clockExtra は常に空
//     （market_profile_actor.js:380-382）であり、`to` を送るとサーバの壁時計とずれて
//     ライブと byte 非等価な MP になる（ISSUE-129 の単一時計）。
//
// 最新 1m 足の供給は合成根が注入する（既存 LiveTickPlayer の updateLastCandle を流用）。
//   `_getCandles` はライブでは「チャートのローソク配列」だが、ここで要るのは**末尾 1 本**だけ
//   である（periodExtra は末尾の time、dispExtra は末尾の close しか読まない）。
//
// 設定の**出所**（どの instance の params か）も本モジュールが持つ（裁定 7・SRP）。合成根の
//   責務は結線だけであり、テンプレート束の記録の形（`indicator_id` / `timeframe_binding` /
//   `params`）を知るのは「テンプレートの MP 設定を写す」役である。束の形が変わる日に直す場所を
//   1 つにする。

/** 借用する MP の指標 ID（テンプレート束の instance を引く鍵）。 */
const MP_INDICATOR_ID = 'market_profile';

/**
 * ライブと同一の MP 取得文脈を組む器を作る。
 *
 * @param {object}   deps
 * @param {Function} deps.MpFetchParams   ライブの写像クラス（公開面から注入）
 * @param {string}   deps.datasetRef      素材（T-10: live と同一データセット固定）
 * @param {string}   deps.timeframe       ラダーの価格軸の足（CHART_TIMEFRAME）
 * @param {Function} deps.getLatestCandle 最新 1m 足 `{time, close}`（無ければ null）
 * @param {Function} deps.nowSec          現在時刻 秒（注入＝実時間に依存させない）
 * @param {*}        [deps.defaultSource] ライブの src 既定（`MP_DEFAULT_SOURCE`。テンプレートに
 *                                        MP が無いときだけ使う。'zp' のリテラルを持たない）
 * @returns {{setParams: Function, setFromBundle: Function, context: Function, settingsKey: Function}}
 */
export function createMpFetchContext({
  MpFetchParams, datasetRef, timeframe, getLatestCandle, nowSec, defaultSource,
} = {}) {
  if (typeof MpFetchParams !== 'function') {
    throw new TypeError('createMpFetchContext: MpFetchParams（ライブの写像）の注入は必須');
  }
  if (typeof getLatestCandle !== 'function') {
    throw new TypeError('createMpFetchContext: getLatestCandle の注入は必須');
  }
  if (typeof nowSec !== 'function') {
    throw new TypeError('createMpFetchContext: nowSec（時計）の注入は必須');
  }

  /** MpFetchParams が要求する host 契約の最小実装（mp_fetch_params.js:14-17）。 */
  const host = {
    _sessions: false,
    _getCandles: () => {
      const latest = getLatestCandle();
      return latest ? [latest] : [];
    },
    _nowSec: nowSec,
    _replayScrub: { isReplay: () => false },
  };

  const params = new MpFetchParams(host);

  /** getContext 相当（ライブでは現在チャート状態の遅延読み取り）。 */
  function base() {
    return { datasetRef, timeframe };
  }

  /**
   * テンプレートの MP instance の params を取り込む。
   *
   * `mode` は表示モードであって取得パラメータではない（白表の外）。ライブでは actor が
   * `_sessions` の状態として持つので、ここでも host のフィールドへ写す。
   */
  function setParams(raw = {}) {
    const values = raw && typeof raw === 'object' ? raw : {};
    host._sessions = values.mode === 'sessions';
    params.set(values);
  }

  /**
   * テンプレート束から、借用に使う MP 設定を取り込む（裁定 7）。
   *
   * 規則: `timeframe_binding` が本器の足に一致する `market_profile` instance の**先頭 1 件**。
   * 不在なら `{src: defaultSource}` だけ（ライブの既定に従う。既定が変わったら追随する）。
   *
   * @param {?object} bundle `readInstanceBundle` の結果（`instances` を読む）
   */
  function setFromBundle(bundle) {
    const instances = bundle && Array.isArray(bundle.instances) ? bundle.instances : [];
    const hit = instances.find(
      (instance) => instance && instance.indicator_id === MP_INDICATOR_ID
        && instance.timeframe_binding === timeframe,
    );
    setParams(hit ? hit.params : { src: defaultSource });
  }

  // MP_FETCH_CONTEXT_SPREAD_ORDER — 並びはライブの refresh（market_profile_actor.js:473-476）と
  //   1 行単位で一致させる。spread は**後勝ち**なので、同じ部品でも順番が違えば別の URL に
  //   なる。値の検定では並びの入れ替わりを捕まえられないため、tests/mp_fetch_context.test.js が
  //   両方のソースを走査して突き合わせる。
  function context() {
    return {
      ...base(),
      ...params.values(),
      ...params.sessionsExtra(),
      ...params.periodExtra(),
      ...params.dispExtra(),
    };
  }

  /**
   * 契機の鍵＝**ユーザーが所有する設定**の同一性（発行判定用）。
   *
   * 文脈（context()）から作ってはならない。context には periodExtra / dispExtra が含まれ、
   * dispExtra の barw は**最新 close から**導かれる（価格由来）。カタログの dispbp 既定は
   * 3.0（catalog_entry.js:97）なので実運用の params にはほぼ常に dispbp が載り、文脈を鍵に
   * すると値動きのたびに鍵が変わる＝ティックのたびに再取得になる。
   * 実測（1 バー枠 60s・再生粒度 100ms・値動きのみ・実 MpFetchParams）: 期待 1 回に対し
   * **600 回発行**。出力（MP 列の見た目）は正しいままなので状態検証では原理的に落ちない
   * 「作ってから捨てる」計算である（絶対命令 §4.1・ISSUE-450 と同型）。
   *
   * 参照実装も同じ分担である: dispExtra は refresh の**内側**で採取されるだけで契機には
   * 関与せず、契機は update_scheduler のバー確定である（market_profile_actor.js:471-476）。
   * したがって鍵は「ユーザーが設定を変えたか」だけを表し、価格追従は発行時の context() が担う。
   */
  function settingsKey() {
    const values = params.values();
    const settings = Object.keys(values).sort().map((k) => `${k}=${String(values[k])}`).join('&');
    // mode は白表の外（values に出ない）が sessions=1 で URL を変えるため、鍵にも入れる。
    return `${host._sessions ? 'sessions' : 'normal'}|${settings}`;
  }

  return { setParams, setFromBundle, context, settingsKey };
}
