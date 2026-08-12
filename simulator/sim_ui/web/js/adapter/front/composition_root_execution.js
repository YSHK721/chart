// 実行指示パネルの合成根（Phase 6 F-8）。
//
// 表示 iframe の合成根（composition_root_front.js）とは責務が別（あちらは完了ジョブの閲覧）。
// ここは「戦略を指定して投入する」入口を組む。結線だけを持ち、DOM 生成はパネル View、HTTP は
// job_submit_client が持つ（SRP）。fetch・doc・host は注入する（実行とテストを分ける）。
//
// 指標候補の単一ソース（名前空間結線・依頼者承認 2026-08-12）: GET /sim/ea-series/{ea_name}。
//   これは backend の受付検証（submit_job E-5）と GenericConditionStrategy が実際に参照する
//   **ea_name 別の registry 系列名**（ema / adx / close ...・build_ea_indicators 単一ソース）を
//   返す。因果カタログの /sim/indicators は別名前空間（MA / hl_range ...）かつ ea_name 非依存で
//   あり、候補源に使うと選んだ指標が投入時 E-5 で全て 400 になる（実測済み）。したがって候補源は
//   /sim/ea-series に固定し、ea_name（指標セット）を変えるたびに選択 EA の系列へ取り直す。

import { createJobSubmitClient } from "./job_submit_client.js";
import { createSimExecutionPanelView } from "./sim_execution_panel_view.js";

/** /sim/ea-series payload から系列名の配列を取り出す。 */
export function eaSeriesNames(payload) {
  const series = (payload && payload.series) || [];
  return series.map((s) => String(s));
}

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

  // 選択中の ea_name の registry 系列を候補へ入れる。取れなくてもパネルは操作可能にする
  // （fail-open だが投入は受付検証 E-5 で守られる）。
  async function refreshCandidates(eaName) {
    if (!eaName) { view.setIndicatorCandidates([]); return; }
    try {
      const payload = await client.loadEaSeries(eaName);
      view.setIndicatorCandidates(eaSeriesNames(payload));
    } catch (_e) {
      view.setIndicatorCandidates([]);
    }
  }

  // 初期候補は選択中（先頭）の ea_name の系列。
  const initialEa = view.elements.eaSel ? String(view.elements.eaSel.value || "") : "";
  await refreshCandidates(initialEa);
  // ea_name（指標セット）を変えたら候補を選択 EA の系列へ取り直す。
  view.onEaChange((eaName) => { refreshCandidates(eaName); });

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

  return { view, client };
}
