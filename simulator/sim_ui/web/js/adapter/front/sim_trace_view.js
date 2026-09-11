// 分析タブの面（View・RUN_TRACE_BASIC_DESIGN §9.1/§9.3）。
//
// 見せるもの: 口座 equity・証拠金・証拠金維持率・保有玉数・DD・事象一覧。
//
// **既存の「Balance」「Drawdown」窓とは別物である**（§9.1・実測）: 既存窓の中身は
//   `agg.balance_curve`（確定トレードの `exit_time` × 走行残高）をバー時刻へ前方補完した
//   もので、窓のラベル自身が「**残高ベース**DD」と名乗っている。equity（含み損益込み）・
//   証拠金・維持率・保有玉数は `report.json` にも `stats.json` にも無い。したがって
//   この面は「既存面の拡張」ではなく、**既存 3 窓の隣に別の量を並べる**ものである。
//
// **列名を手書きしない**（§9.5 と同型の規律）: どの系列を描くかはサーバが `extent` で
//   配る `columns` から導く。front に一覧を書けば宣言が 2 箇所になり、片方だけ取り残される
//   （まさに `sim_tabs_view.test.js` が抱えていた欠陥）。`time` だけは横軸なので系列にしない。
//
// **時刻は epoch ミリ秒**（§9.0）: `Date` がそのまま受ける。秒精度だった時期は実ティック
//   1 ヶ月 run の 39.3% の行が同じ時刻になり、同一秒内の equity / 維持率の谷が読めなかった。
//   ここで同一時刻を代表値へ潰す（既存 `chart.js` の `dedupeCurve` の形）ことは**しない**
//   ——症状の出る条件を避ける対症療法であり、DD 分析が壊れる。
//
// **半端な絵を残さない**: 取得に失敗したら系列を消して掲示だけを出す。空のチャートを
//   出すと「記録が無い run」と「取得に失敗」が見分けられない。
//
// fake DOM 前提: querySelector は使わず、要素参照を JS 側で保持する。
// lwc・CSS には触らない（DOM だけを知る）。

/** 分析タブの名前（`sim_tabs_view.SIM_TAB_NAMES` に含まれること＝結線の表明）。 */
export const SIM_TRACE_TAB_NAME = "analysis";

/** 横軸に使う列（系列としては描かない）。サーバの宣言の中の 1 つを名指しするだけ。 */
const TIME_COLUMN = "time";

/** 事象一覧が 0 件のときの掲示（空欄と区別する）。 */
const NO_EVENTS_TEXT = "この窓に事象はありません";

/** 分析タブの中身を生成・描画する View を返す。 */
export function createSimTraceView({ doc } = {}) {
  let root = null;
  let host = null;
  let seriesHost = null;
  let ddHost = null;
  let eventsHost = null;
  let problemHost = null;

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
    return node;
  };

  const clear = (node) => {
    if (!node) return;
    while (node.children.length) node.removeChild(node.children[node.children.length - 1]);
    node.textContent = "";
  };

  function build() {
    const container = el("div", { className: "trace-analysis" });
    problemHost = el("div", { className: "trace-problem" });
    ddHost = el("div", { className: "trace-dd" });
    seriesHost = el("div", { className: "trace-series" });
    eventsHost = el("div", { className: "trace-events" });
    container.appendChild(problemHost);
    container.appendChild(ddHost);
    container.appendChild(seriesHost);
    container.appendChild(eventsHost);
    return container;
  }

  /** 1 系列ぶんの行（見出し＋値の並び）を組む。値の意味は解釈しない。 */
  function buildSeriesRow(name, values) {
    const row = el("div", { className: "trace-series-row", dataset: { series: name } });
    row.appendChild(el("span", { className: "trace-series-name", textContent: name }));
    const count = Array.isArray(values) ? values.length : 0;
    row.appendChild(el("span", {
      className: "trace-series-count", textContent: `${count} 点`,
    }));
    if (count > 0) {
      const numeric = values.filter((v) => typeof v === "number" && Number.isFinite(v));
      if (numeric.length > 0) {
        row.appendChild(el("span", {
          className: "trace-series-range",
          textContent: `${Math.min(...numeric)} 〜 ${Math.max(...numeric)}`,
        }));
      }
    }
    return row;
  }

  function buildEventRow(event) {
    const row = el("div", {
      className: "trace-event",
      dataset: { eventKind: event.kind, eventTime: String(event.time) },
    });
    row.appendChild(el("span", {
      className: "trace-event-time",
      // epoch ミリ秒をそのまま `Date` へ渡す（§9.0 の是正でこれが可能になった）。
      textContent: new Date(event.time).toISOString(),
    }));
    row.appendChild(el("span", { className: "trace-event-kind", textContent: event.kind }));
    row.appendChild(el("span", {
      className: "trace-event-value",
      textContent: `${event.previous} → ${event.value}`,
    }));
    return row;
  }

  return {
    /** タブ名（合成根が挿す先を選ぶための面）。 */
    tabName: SIM_TRACE_TAB_NAME,

    isMounted() { return root !== null; },

    /** 器を `target` の下へ挿す（二重 mount は無視・同じ root を返す）。 */
    mount(target) {
      if (root) return root;
      host = target;
      root = build();
      host.appendChild(root);
      return root;
    },

    unmount() {
      if (root && host) host.removeChild(root);
      root = host = seriesHost = ddHost = eventsHost = problemHost = null;
    },

    /**
     * `extent`（サーバが配る宣言）と `points`（窓の中身）を描く。
     *
     * 描く系列は **`extent.columns` から導く**（front に一覧を持たない）。横軸の
     * `time` だけは系列にしない。`points.columns` に無い列は描かない——値を発明しない。
     */
    render({ extent, points } = {}) {
      if (!root) return;
      clear(problemHost);
      clear(ddHost);
      clear(seriesHost);
      clear(eventsHost);

      const served = (extent && extent.columns) || [];
      const columns = (points && points.columns) || {};
      for (const name of served) {
        if (name === TIME_COLUMN) continue;
        if (!(name in columns)) continue;
        seriesHost.appendChild(buildSeriesRow(name, columns[name]));
      }

      const drawdown = (points && points.drawdown) || {};
      const parts = Object.entries(drawdown).map(([k, v]) => `${k}=${v}`);
      ddHost.textContent = parts.join("  ");

      const events = (points && points.events) || [];
      if (events.length === 0) {
        eventsHost.textContent = NO_EVENTS_TEXT;
      } else {
        for (const event of events) eventsHost.appendChild(buildEventRow(event));
      }
    },

    /**
     * 取得できなかったことを掲示する。**描きかけを消してから**出す。
     *
     * 半端な絵を残すと「記録が無い run」と「取得に失敗」が見分けられない。上限超過
     * （413）もここへ来る——サーバの文言（何行あり上限が何行か）をそのまま見せる。
     */
    showProblem(message) {
      if (!root) return;
      clear(seriesHost);
      clear(ddHost);
      clear(eventsHost);
      clear(problemHost);
      problemHost.textContent = String(message || "");
    },
  };
}
