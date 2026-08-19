// 実行指示パネルの合成根（Phase 6 F-8 / Phase 9 S1）。
//
// 表示 iframe の合成根（composition_root_front.js）とは責務が別（あちらは完了ジョブの閲覧）。
// ここは「実行条件を指定して投入する」入口を組む。結線だけを持ち、DOM 生成はパネル View、
// HTTP は job_submit_client が持つ（SRP）。fetch・doc・host は注入する（実行とテストを分ける）。

import { createJobSubmitClient } from "./job_submit_client.js";
import { createSettingsSchemaClient } from "./settings_schema_client.js";
import { createSimEaInputsPanelView } from "./sim_ea_inputs_panel_view.js";
import { createSimRunActionView } from "./sim_run_action_view.js";
import { createSimSchemaFallbackView } from "./sim_schema_fallback_view.js";
import { buildSubmission, resolveProfile } from "./sim_submission_builder.js";
import { createSimTesterSettingsPanelView } from "./sim_tester_settings_panel_view.js";

/** 投入した job_id を閲覧するビューアの URL（report_view.html の `?job=` dispatch）。
 *  相対クエリのみ（同一文書内で dispatch＝フォーム→ビューアに切り替わる）。 */
export function reportViewUrl(jobId) {
  return `?job=${encodeURIComponent(jobId)}`;
}

/**
 * 実行指示パネルを host へ組み、投入クライアントと結線する。
 *
 * @param {Document} doc          注入 DOM
 * @param {Element}  host         器を挿す先
 * @param {function} fetch        注入 fetch（同一オリジン相対）
 * @param {string[]} eaCandidates ea_name（指標セット）候補
 * @param {function} onSubmitted  投入成功時のコールバック（job view を受ける・任意）
 * @param {function} onError      投入失敗時のコールバック（任意）
 */
export async function mountSimExecutionPanel({
  doc, host, fetch: fetchFn, eaCandidates, onSubmitted, onError, navigate,
} = {}) {
  const client = createJobSubmitClient({ fetch: fetchFn });
  const schemaClient = createSettingsSchemaClient({ fetch: fetchFn });
  // Tester Settings パネル（Phase 8）。schema が取れなくても**器は出す**（fail-open・
  // run-options と同じ流儀）。取れなければ候補 0 のまま理由を表示し、投入は旧フォーム
  // （指標セット欄・初期資金欄）が権威のまま成立する＝現行経路の本文と byte 等価。
  const testerView = createSimTesterSettingsPanelView({ doc });
  testerView.mount(host);

  // run config フォームの選択肢（データセット profile＋ea_name 一覧）を単一ソースから取る。
  // 取れなくてもパネルは出す（fail-open）。profile が空だと投入は E-5b で弾かれるが、
  // 「サーバが落ちた」ではなく「実行条件を取得できない」と分かる状態にする。
  let datasets = [];
  let eaNames = Array.isArray(eaCandidates) ? eaCandidates : [];
  try {
    const opts = await client.loadRunOptions();
    datasets = (opts && opts.datasets) || [];
    // eaCandidates 未指定なら run-options の ea_names を候補にする（単一ソース）。
    if (!Array.isArray(eaCandidates)) eaNames = (opts && opts.ea_names) || [];
  } catch (_e) {
    datasets = [];
  }
  // 銘柄候補はデータセット一覧から引く（front リテラル 0）。同じ銘柄の複数データセットは
  // 1 つに畳む——候補は「選べる銘柄」であって「データセットの数」ではない。
  const symbolCandidates = [...new Set(datasets.map((d) => String(d.symbol)))];

  // 実行対象（銘柄・EA・口座・設定ブロック）の供給元を 1 つだけ立てる（Phase 9 S3）。
  //   schema が取れた  → M1 Tester Settings 面が供給元。縮退面は**作らない**。
  //   取れなかった      → M4 縮退面を立てて供給元にする。Tester 面の器は残り、なぜ設定を
  //                      組めないのかを画面に出し続ける（fail-open）。
  // 「欄を出してから removeChild で消す」形は撤去した——消し忘れれば同一概念の入力欄が
  // 2 つ並び、どちらの値で実行されたのかが画面から判断できなくなる。
  let fallbackView = null;
  let subjectSource = null;
  testerView.setSymbolCandidates(symbolCandidates);
  try {
    testerView.setSchema(await schemaClient.load());
    subjectSource = testerView;
  } catch (e) {
    testerView.setSchema(null);
    fallbackView = createSimSchemaFallbackView({ doc });
    fallbackView.setSymbolCandidates(symbolCandidates);
    fallbackView.setEaCandidates(eaNames);
    fallbackView.mount(host);
    subjectSource = fallbackView;
    // 理由を捨てない。schema が来ない run は縮退面で動き続けるため、画面だけを見ても
    // 「なぜ Tester パネルが空なのか」が分からない（無音の縮退）。パネル上の掲示に加えて
    // 開発者コンソールにも残す。
    console.warn(`settings-schema を取得できません: ${(e && e.message) || e}`);
  }

  // EA パラメータ面（M2）。実行仕様の EA 側パラメータはこの面だけが所有する。
  const eaInputsView = createSimEaInputsPanelView({ doc });
  eaInputsView.mount(host);
  // 実行指示面（M3）。責務はスタートと結果導線だけ（本文も HTTP も知らない）。
  const view = createSimRunActionView({ doc });
  view.mount(host);

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

  view.onStart(async () => {
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
    try {
      const result = await client.submit(body);
      if (result && result.job_id) view.showResultLink(result.job_id);
      if (onSubmitted) onSubmitted(result);
    } catch (e) {
      if (onError) onError(e);
    }
  });

  return { view, client, testerView, eaInputsView, fallbackView, subjectSource, schemaClient };
}
