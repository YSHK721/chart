// 実行指示パネルの合成根（Phase 6 F-8 / Phase 9 S1）。
//
// 表示 iframe の合成根（composition_root_front.js）とは責務が別（あちらは完了ジョブの閲覧）。
// ここは「実行条件を指定して投入する」入口を組む。結線だけを持ち、DOM 生成はパネル View、
// HTTP は job_submit_client が持つ（SRP）。fetch・doc・host は注入する（実行とテストを分ける）。

import { createJobSubmitClient } from "./job_submit_client.js";
import { createSettingsSchemaClient } from "./settings_schema_client.js";
import { createSimExecutionPanelView } from "./sim_execution_panel_view.js";
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
  const view = createSimExecutionPanelView({ doc });
  view.mount(host);
  if (Array.isArray(eaCandidates)) view.setEaCandidates(eaCandidates);

  // run config フォームの選択肢（データセット profile＋ea_name 一覧）を単一ソースから入れる。
  // 取れなくてもパネルは出す（fail-open）。profile が空だと投入は E-5b で弾かれるが、
  // 「サーバが落ちた」ではなく「実行条件を取得できない」と分かる状態にする。
  try {
    const opts = await client.loadRunOptions();
    view.setRunOptions((opts && opts.datasets) || []);
    // eaCandidates 未指定なら run-options の ea_names を候補にする（単一ソース）。
    if (!Array.isArray(eaCandidates)) view.setEaCandidates((opts && opts.ea_names) || []);
  } catch (_e) {
    view.setRunOptions([]);
  }

  const goTo = navigate || ((url) => { if (typeof location !== "undefined") location.href = url; });

  // Tester Settings の schema を単一ソースから入れる。取れたときだけパネルを settings の
  // 供給元として結線する（取れない構成で結線すると、候補 0 の Expert から空の投入本文が
  // 出来てしまう）。取得失敗でもパネル自体は残り、理由が画面に出る。
  try {
    testerView.setSchema(await schemaClient.load());
    view.setTesterPanel(testerView);
  } catch (e) {
    testerView.setSchema(null);
    // 理由を捨てない。schema が来ない run は旧フォームで動き続けるため、画面だけを見ても
    // 「なぜ Tester パネルが空なのか」が分からない（無音の縮退）。パネル上の掲示に加えて
    // 開発者コンソールにも残す。
    console.warn(`settings-schema を取得できません: ${(e && e.message) || e}`);
  }

  // 投入成功時の「結果を見る」導線。**自動遷移しない**（ビュー自動介入禁止）。
  // ユーザーがこのボタンを押したときだけ `?job=<id>` の dispatch でビューアへ切り替える。
  function showResultLink(jobId) {
    let link = view.elements.viewResult;
    if (!link) {
      link = doc.createElement("button");
      link.id = "execViewResult";
      link.className = "exec-view-result";
      link.type = "button";
      link.textContent = "結果を見る";
      link.addEventListener("click", () => { if (link._jobId) goTo(reportViewUrl(link._jobId)); });
      host.appendChild(link);
      view.elements.viewResult = link;
    }
    link._jobId = jobId;
    return link;
  }

  view.onSubmit(async (body) => {
    try {
      const result = await client.submit(body);
      if (result && result.job_id) showResultLink(result.job_id);
      if (onSubmitted) onSubmitted(result);
    } catch (e) {
      if (onError) onError(e);
    }
  });

  return { view, client, testerView, schemaClient };
}
