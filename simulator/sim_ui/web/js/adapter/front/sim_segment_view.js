// 区間トグル（View・F-7 点15）。**縮退規則の唯一の所有者**。
//
// 役割: 移植元 index.html:16-20 の #segSel（区間トグル）を生成する。移植元は IS/OOS の
//   2 区間が常にある前提だが、sim の実ジョブは単一 run（segments={"single"}）である。
//   区間が 1 つしか無いときは**トグルそのものを出さない**（P9: #segSel 不在）。この
//   「区間が複数あるか」の判断はここ 1 か所だけが持つ——sim_display_view も合成根も
//   別途 segKeys を数えて分岐すると、規則が 2 本になり片方だけ腐る（§認知負荷）。
//
// 選択の通知は onSelect コールバック（移植元 main.js buildSegToggle と同流儀）。現在区間の
//   クリックは通知しない（`b.dataset.seg !== CUR_SEG`）。表示の張り替え（.on）は setCurrent が
//   担う（移植元 selectSegment の segbtn 部）。lwc・report.json・CSS には触らない。

/** segbtn のラベル（移植元 index.html:18-19）。未知キーはキー名をそのまま出す。 */
const SEG_LABELS = Object.freeze({ is: "IS 学習", oos: "OOS 検証" });

/** 区間トグルを生成・更新する View を返す。 */
export function createSimSegmentView({ doc } = {}) {
  let root = null;
  const buttons = {};

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
    return node;
  };

  return {
    /** 生成した #segSel（縮退時は null）。 */
    elements: { root: null },

    /** 区間トグルを host へ描く。segKeys が 1 つ以下なら**何も描かない**（縮退）。 */
    render({ host, segKeys, current, onSelect } = {}) {
      const keys = segKeys || [];
      if (keys.length < 2) {
        // 縮退: トグルを持たない（P9）。既に描いていたものは残さない。
        if (root && root.parentNode) root.parentNode.removeChild(root);
        root = null;
        this.elements.root = null;
        return null;
      }
      const seg = el("span", { id: "segSel", className: "segwrap" });
      seg.appendChild(el("span", { className: "seg-label", textContent: "区間" }));
      for (const key of keys) {
        const btn = el("span", {
          className: "segbtn", textContent: SEG_LABELS[key] || key, dataset: { seg: key },
        });
        if (key === current) btn.classList.add("on");
        btn.addEventListener("click", () => {
          // 現在区間の再クリックは無視（移植元 main.js buildSegToggle と同一）。
          if (btn.dataset.seg !== this._current() && onSelect) onSelect(key);
        });
        buttons[key] = btn;
        seg.appendChild(btn);
      }
      root = seg;
      this.elements.root = seg;
      this._cur = current;
      host.appendChild(seg);
      return seg;
    },

    /** 現在区間の .on を張り替える（移植元 selectSegment の segbtn 部）。縮退時は no-op。 */
    setCurrent(key) {
      this._cur = key;
      for (const [k, btn] of Object.entries(buttons)) {
        btn.classList.toggle("on", k === key);
      }
    },

    /** 現在区間（クリック抑止の比較に使う内部状態）。 */
    _current() { return this._cur; },
    _cur: undefined,
  };
}
