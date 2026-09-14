// 分析タブの合成根（front・RUN_TRACE_BASIC_DESIGN §9.1/§9.2）。
//
// 役割: 分析ペインへ面（`sim_trace_view`）を挿し、通信（`trace_analysis_client`）から
//   得た応答を渡すこと——結線だけである。何を描くかは面が、何を返すかはサーバが持つ。
//
// **なぜ表示合成根（`composition_root_front.js`）に書かないか**（実測に基づく理由）:
//   同ファイルは移植元実体を `/sim/report-js/` 配下の**絶対 URL**で import するため、
//   node からは読み込めない（`ERR_MODULE_NOT_FOUND`）。結線を同ファイルへ書くと、
//   「呼んでいること」を確かめる手段がブラウザ E2E だけになる。ISSUE-291 の壊れ方
//   （受け口はあるのに呼ばれない）は静かなので、node で測れる場所に置く。
//   先例は `composition_root_execution.js`（同じ理由で別合成根になっている）。
//
// **窓を front が発明しない**（§9.2）: 最初に問う窓は `extent.suggested_window` を
//   そのまま使う。**比例配分で推測しない**——当初は「上限 ÷ 全行数」の比で時間幅を
//   決めていたが、ティック密度は一様でない（週末・立会時間）。実ティック 1 ヶ月 run
//   （1,036,394 行）でその比から出した窓には 59,030 行が入り、初回表示が 413 になった
//   （実測 2026-09-10）。行の分布を持つのはサーバなので、サーバが測って答える。
//   413 を受けてから絞り直すのも往復を 1 つ捨てる形であり、絶対命令に当たる。
//
// **間引かない**: 上限を超える窓は狭める。同一時刻を代表値へ潰す・N 点ごとに拾うは
//   §9.0 が是正した欠陥と同型で、equity / 維持率の谷が消えて DD 分析が壊れる。
//
// **失敗で呼出側を巻き込まない**: 分析が取れなくても throw しない（掲示に留める）。
//   先例 `run_job.py:311-323`「表示の失敗で成功した計算を捨てない」と同じ規律であり、
//   ここで throw すると分析 API の不調で結果ビューア全体が fail-stop になる。

import { createTraceAnalysisClient } from "./trace_analysis_client.js";
import { createSimTraceView } from "./sim_trace_view.js";

/**
 * 最初に問う窓 `[start, end)`（epoch ミリ秒）を `extent` から**読む**。
 *
 * 計算しない: どの窓なら返せるかを知っているのは行の分布を持つサーバであり、
 * その答えは `extent.suggested_window` に入っている。ここで比を掛け直すと、
 * 同じ判断の 2 つ目の所有者ができて片方が必ず外れる。
 *
 * サーバが窓を示さない（記録が無い run）ときは `[null, null]`＝無制限で問う。
 * 記録 0 件なら 0 行が返るだけで、値を発明したことにはならない。
 */
export function initialWindow(extent) {
  const suggested = (extent && extent.suggested_window) || {};
  const start = suggested.start;
  const end = suggested.end;
  if (start === null || start === undefined || end === null || end === undefined) {
    return [null, null];
  }
  return [start, end];
}

/**
 * 分析ペインを組んで描く。
 *
 * @param {Document} doc     子文書の DOM
 * @param {Element}  pane    分析ペイン（`sim_tabs_view` が作った `data-pane="analysis"`）
 * @param {string}   jobId   表示対象ジョブ
 * @param {Function} fetch   注入する fetch（未指定なら globalThis.fetch）
 * @returns {{drawn: function, view: object, destroy: function}}
 */
export async function mountTraceAnalysis({ doc, pane, jobId, fetch: fetchFn } = {}) {
  const view = createSimTraceView({ doc });
  view.mount(pane);
  const client = createTraceAnalysisClient({ fetch: fetchFn });
  let drawn = false;

  try {
    const extent = await client.extent(jobId);
    const [start, end] = initialWindow(extent);
    const points = await client.points(jobId, start, end);
    view.render({ extent, points });
    drawn = true;
  } catch (e) {
    // 取れなかったことを掲示する（空のペインにしない＝「記録が無い run」と混同しない）。
    view.showProblem(e && e.message ? e.message : "実行トレースを取得できません");
  }

  return {
    /** 描けたか（診断・検定用）。 */
    drawn() { return drawn; },
    /** 面（呼出側が窓を変えて描き直すための口）。 */
    view,
    destroy() { view.unmount(); },
  };
}
