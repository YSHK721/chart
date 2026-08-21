// 投入フォームの合成根（Phase 6 F-8 / Phase 9 S1〜S6 / 段階 3 §19.6）。
//
// 表示 iframe の合成根（composition_root_front.js）とは責務が別（あちらは完了ジョブの閲覧）。
// ここは「実行条件を指定して投入する」入口を組む。結線だけを持ち、DOM 生成はパネル View、
// HTTP は job_submit_client / job_status_client が持つ（SRP）。fetch・doc・host・時計は
// 注入する（実行とテストを分ける）。

import { createJobStatusClient } from "./job_status_client.js";
import { createJobSubmitClient } from "./job_submit_client.js";
import { createSettingsSchemaClient } from "./settings_schema_client.js";
import { createSimEaInputsPanelView } from "./sim_ea_inputs_panel_view.js";
import { createSimRunActionView } from "./sim_run_action_view.js";
import { createSimRunStatusView } from "./sim_run_status_view.js";
import { createSimRunLayoutView } from "./sim_run_layout_view.js";
import { createSimSchemaFallbackView } from "./sim_schema_fallback_view.js";
import { buildSubmission, resolveProfile, symbolCandidatesOf } from "./sim_submission_builder.js";
import { createSimTesterSettingsPanelView } from "./sim_tester_settings_panel_view.js";

/** 投入した job_id を閲覧するビューアの URL（report_view.html の `?job=` dispatch）。
 *  相対クエリのみ（同一文書内で dispatch＝フォーム→ビューアに切り替わる）。 */
export function reportViewUrl(jobId) {
  return `?job=${encodeURIComponent(jobId)}`;
}

/**
 * 投入フォームの 4 面（M1/M2/M3・schema を取れなければ M4）と掲示面（M6）を host へ組み、
 * 投入クライアント（M5 経路）・状態監視（M7）と結線する。
 *
 * @param {Document} doc          注入 DOM
 * @param {Element}  host         器を挿す先
 * @param {function} fetch        注入 fetch（同一オリジン相対）
 * @param {string[]} eaCandidates ea_name（指標セット）候補。未指定なら run-options から引く
 * @param {function} onSubmitted  投入成功時のコールバック（job view を受ける・任意）
 * @param {function} onError      投入失敗時のコールバック（任意）
 * @param {function} navigate     遷移の実行（注入・任意。既定は location.href への代入）
 * @param {function} setTimeout   状態監視の時計（注入・任意。既定は globalThis）
 * @param {function} clearTimeout 状態監視の時計の取消（注入・任意。既定は globalThis）
 */
export async function mountSimExecutionPanel({
  doc, host, fetch: fetchFn, eaCandidates, onSubmitted, onError, navigate,
  setTimeout: setTimeoutFn, clearTimeout: clearTimeoutFn,
} = {}) {
  const client = createJobSubmitClient({ fetch: fetchFn });
  const statusClient = createJobStatusClient({
    fetch: fetchFn, setTimeout: setTimeoutFn, clearTimeout: clearTimeoutFn,
  });
  const schemaClient = createSettingsSchemaClient({ fetch: fetchFn });

  // 掲示面（M6）は器の組み立てより**先に作る**: 面の構築が落ちたときに理由を出す先が
  // ここだからである。挿す位置は通常経路では M3 の直下、mount 段が落ちたときだけ最上部
  // （§19.6 R2）。二重に挿さないよう、挿したかどうかはこの 1 箇所で持つ。
  const statusView = createSimRunStatusView({ doc });
  let statusMounted = false;
  function mountStatus({ atTop = false } = {}) {
    if (statusMounted) return;
    // 通常経路は入力列（スタートの直下＝§19.6 R2）。mount 段が落ちたときは版面がまだ無い
    // ので host の最上部へ出す（理由を出す先を必ず残す）。
    statusView.mount(atTop ? host : (layout.inputsHost() || host), { atTop });
    statusMounted = true;
  }

  // 版面（ISSUE-441）: 設定と入力を横に分ける器。面の mount 先はここから取る。
  //   器が作れない環境（fake DOM の縮退等）でも面は host へ出す＝画面が空にならない。
  const layout = createSimRunLayoutView({ doc });

  // 面の参照（mount 段が落ちた場合は null のまま返す＝呼出側が受け取る形は変えない）。
  let testerView = null;
  let fallbackView = null;
  let eaInputsView = null;
  let view = null;
  let subjectSource = null;
  // 実行条件（データセット profile＋ea_name 一覧）。結線段でも使うため try の外に置く。
  let datasets = [];
  let eaNames = Array.isArray(eaCandidates) ? eaCandidates : [];
  // 動いている状態監視の停止関数（同時 1 本）。解体口から止めるため try の外に置く。
  let stopWatch = null;

  /**
   * 組んだものを止める（🟡-4）。現在は状態監視の時計だけが「動き続ける物」である。
   *
   * 組み立て側が start する物には、呼出側が止める手段が要る。無いと、画面を捨てても
   * 時計だけが空回りし続ける（実測: 検定 1 ファイルで console.error 7 行の漏出と実 timer
   * による約 3.0 秒の待ち）。何度呼んでも安全にする（後片付けの順序を呼出側に強いない）。
   */
  function dispose() {
    if (stopWatch) { stopWatch(); stopWatch = null; }
  }

  /** 呼出側へ返す面の参照。成功・失敗のどちらの出口も**この 1 箇所**から作る。
   *  出口ごとに object リテラルを書くと、片方にだけ面を足したときに「構成によって
   *  返る形が違う」状態が黙って生まれる（呼出側は分岐を知らないまま undefined を掴む）。 */
  const panelRefs = () => ({
    view, client, testerView, eaInputsView, fallbackView, subjectSource, schemaClient, statusView,
    dispose,
  });

  // 組み立て（mount 段）**と結線段**の全体を包む（§19.6 B4・🔴-1）。呼出側
  // （`report_view.html`）は catch を持たないため、ここで抜けた例外は誰にも捕まらない。
  // 落ち方は 2 通りあり、どちらも無音になる:
  //   mount 段  — 画面に何も出ない（白い画面）
  //   結線段    — 4 面は**完成して見える**のに、スタートの購読者が登録されておらず、
  //               押しても何も起きない（掲示も console も空＝死んだフォーム）
  // 後者は前者より悪い。壊れていることが画面から分からないためである。どちらの場合も
  // 掲示面だけは出し、理由を画面と開発者コンソールの両方に残す（握り潰し禁止）。
  try {
    // Tester Settings パネル（Phase 8）。schema が取れなくても**器は出す**（fail-open・
    // run-options と同じ流儀）。取れなければ候補 0 のまま理由を表示し、投入は旧フォーム
    // （指標セット欄・初期資金欄）が権威のまま成立する＝現行経路の本文と byte 等価。
    layout.mount(host);
    const settingsHost = layout.settingsHost() || host;
    const inputsHost = layout.inputsHost() || host;

    testerView = createSimTesterSettingsPanelView({ doc });
    testerView.mount(settingsHost);

    // run config フォームの選択肢を単一ソースから取る。取れなくてもパネルは出す
    // （fail-open）。profile が空だと投入は E-5b で弾かれるが、「サーバが落ちた」ではなく
    // 「実行条件を取得できない」と分かる状態にする。
    try {
      const opts = await client.loadRunOptions();
      datasets = (opts && opts.datasets) || [];
      // eaCandidates 未指定なら run-options の ea_names を候補にする（単一ソース）。
      if (!Array.isArray(eaCandidates)) eaNames = (opts && opts.ea_names) || [];
    } catch (_e) {
      datasets = [];
    }
    // 銘柄候補はデータセット一覧から引く（front リテラル 0）。引き方そのものは規則なので
    // M5 が所有する（ここは呼ぶだけ＝合成根は結線しか持たない）。
    const symbolCandidates = symbolCandidatesOf(datasets);

    // 実行対象（銘柄・EA・口座・設定ブロック）の供給元を 1 つだけ立てる（Phase 9 S3）。
    //   schema が取れた  → M1 Tester Settings 面が供給元。縮退面は**作らない**。
    //   取れなかった      → M4 縮退面を立てて供給元にする。Tester 面の器は残り、なぜ設定を
    //                      組めないのかを画面に出し続ける（fail-open）。
    // 「欄を出してから removeChild で消す」形は撤去した——消し忘れれば同一概念の入力欄が
    // 2 つ並び、どちらの値で実行されたのかが画面から判断できなくなる。
    testerView.setSymbolCandidates(symbolCandidates);
    try {
      testerView.setSchema(await schemaClient.load());
      subjectSource = testerView;
    } catch (e) {
      testerView.setSchema(null);
      fallbackView = createSimSchemaFallbackView({ doc });
      fallbackView.setSymbolCandidates(symbolCandidates);
      fallbackView.setEaCandidates(eaNames);
      fallbackView.mount(settingsHost);
      subjectSource = fallbackView;
      // 理由を捨てない。schema が来ない run は縮退面で動き続けるため、画面だけを見ても
      // 「なぜ Tester パネルが空なのか」が分からない（無音の縮退）。パネル上の掲示に加えて
      // 開発者コンソールにも残す。
      console.warn(`settings-schema を取得できません: ${(e && e.message) || e}`);
    }

    // EA パラメータ面（M2）。実行仕様の EA 側パラメータはこの面だけが所有する。
    eaInputsView = createSimEaInputsPanelView({ doc });
    eaInputsView.mount(inputsHost);
    // 実行指示面（M3）。責務はスタートと結果導線だけ（本文も HTTP も知らない）。
    view = createSimRunActionView({ doc });
    view.mount(inputsHost);
    // 掲示面はスタートの**直下**（§19.6 R2）: 押した結果がその場に出ないと、投入が通った
    // のか拒まれたのかを画面から判断できない（ISSUE-423）。
    mountStatus();

    const goTo = navigate || ((url) => { if (typeof location !== "undefined") location.href = url; });

    // 実行対象データセットは**銘柄から**引く（データセット選択という sim 独自の概念を出さない）。
    // 解決できたときだけ供給元へ渡す: 解決できない銘柄で既定へ戻すと、利用者が打った値が
    // 黙って書き換わる（ビュー自動介入の禁止）。解決できない間は直前の profile を保ち、
    // 不一致は供給元の警告が画面に出す。
    let runProfile = null;
    function syncRunProfile() {
      const next = resolveProfile(datasets, subjectSource.selectedSymbol());
      if (next === null || next === runProfile) return;
      runProfile = next;
      subjectSource.setRunProfile(runProfile);
    }
    subjectSource.onSymbolChange(() => { syncRunProfile(); });
    syncRunProfile();

    // 投入成功時の「結果を見る」導線。**自動遷移しない**（ビュー自動介入禁止）。導線の DOM は
    // 実行指示面が持ち、ここは「押されたらどこへ行くか」だけを決める。
    view.onViewResult((jobId) => { goTo(reportViewUrl(jobId)); });

    // 実行状態の監視は**同時 1 本**（§19.6 S4）。実行指示面は再投入を許すため、落とさずに
    // 新しい監視を足すと、前の run の状態が新しい run の掲示を上書きし続ける
    // （停止関数 `stopWatch` は解体口と共有するため上で宣言している）。

    // 直近に掲示した状態。監視を諦めたときも「どの状態まで見えていたか」を残す（監視が
    // 止まっただけで、ジョブが終わったわけではない＝終端と書かない）。
    let lastStatus = null;
    // 投入の通番（🟡-1）。「今どの run を見ているか」を**押した時点**で決める。
    //
    // なぜ通番が要るか: 前の監視を落とす判定を「監視が張られているか」で行うと、監視が
    // 張られるのは応答が返ってからなので、応答前の二度押しでは落とす対象がまだ無く停止が
    // 空振りする（実測: POSTs=2 / 監視 2 本）。到着順もネットワーク次第であり「最後に
    // 届いた方が新しい」とは限らない。押した順番だけが確かな順序である。
    // ボタンの無効化（UI 挙動の変更＝承認事項）は行わず、**遅れて届いた応答を捨てる**。
    let submitSeq = 0;

    /** この応答が現在の run のものか（古い run の応答は掲示にも監視にも使わない）。 */
    const isCurrentRun = (seq) => seq === submitSeq;

    function onWatchUpdate(seq, update) {
      if (!isCurrentRun(seq)) return;
      if (update && update.error) {
        // 止まったのは監視であってジョブではない。「実行中…」のままにすると来ない更新を
        // 待たせ、「終了」にすると終わっていないジョブを終わったことにする（🟡-3）。
        statusView.showWatchAbandoned({ status: lastStatus, failure_reason: update.error });
        console.error(update.error);
        return;
      }
      lastStatus = update && update.status;
      statusView.showJobState(update);
    }

    // コールバック**全体**を try で包む（§19.6 B2）。本文の組立（供給元の読み出し・M5 の
    // 純関数）を try の外に置くと、そこで落ちた例外は誰にも捕まらず、画面は押しても何も
    // 起きないまま無音になる（実測済みの欠陥）。失敗は必ず掲示し、開発者コンソールにも残す。
    view.onStart(async () => {
      // 押した時点で通番を進める（この 1 行が「現在の run」の定義）。
      submitSeq += 1;
      const seq = submitSeq;
      try {
        if (stopWatch) { stopWatch(); stopWatch = null; }
        lastStatus = null;
        statusView.showSubmitting();
        // 本文の組み立ては純関数 1 箇所（M5）。ここは 3 つの供給元を渡すだけである。
        const derived = subjectSource.derivedBacktest();
        const body = buildSubmission({
          profile: runProfile,
          subject: {
            ea_name: derived.ea_name,
            initial_deposit: derived.initial_deposit,
            settings: subjectSource.buildSettings(),
          },
          inputs: eaInputsView.values(),
        });
        const result = await client.submit(body);
        // 画面へ触れるのは現在の run だけ（遅れて届いた古い応答は掲示も導線も動かさない）。
        // 購読口（onSubmitted / onError）は投入ごとに従来どおり呼ぶ＝外向きの契約は不変。
        if (isCurrentRun(seq)) {
          if (result && result.job_id) view.showResultLink(result.job_id);
          statusView.showAccepted({
            job_id: result && result.job_id, status: result && result.status,
          });
          lastStatus = result && result.status;
          if (result && result.job_id) {
            stopWatch = statusClient.watch(result.job_id, (update) => onWatchUpdate(seq, update));
          }
        }
        if (onSubmitted) onSubmitted(result);
      } catch (e) {
        const message = (e && e.message) || String(e);
        if (isCurrentRun(seq)) statusView.showRejected({ message, status: e && e.status });
        console.error(`投入できません: ${message}`);
        // 既存の購読口は従来どおり呼ぶ（掲示の追加で契約を変えない＝後方互換）。
        if (onError) onError(e);
      }
    });

  } catch (e) {
    const message = (e && e.message) || String(e);
    console.error(`投入フォームを組み立てられません: ${message}`);
    try {
      // 途中まで組めた面が既に居る（実行指示面の手前で落ちた等）。理由をその**下**に
      // 置くと、壊れた器に埋もれて見つけられない。この経路だけ最上部へ挿す（§19.6 R2）。
      mountStatus({ atTop: true });
      statusView.showFatal(message);
    } catch (_e) {
      // 掲示面すら挿せない（host そのものが壊れている）。理由は既に console に残っている。
    }
    // 実行指示面は**組めていない**（生成の途中で落ちた場合は参照だけが残る）。押せない面を
    // 返すと、呼出側は結線済みだと誤認する。この出口では必ず落とす。
    view = null;
    return panelRefs();
  }
  return panelRefs();
}
