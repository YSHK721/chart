// 投入フォームの版面（View・ISSUE-441）。**器だけ**を持ち、中身は各面が持つ。
//
// なぜ在るか（依頼者指示 2026-08-22・実測 1990×約 260px の下部ペイン）:
//   4 面（Tester Settings / Inputs / 実行指示 / 掲示）は body の縦 flex へ順に積まれ、幅は
//   `min(860px, 100% - 24px)` の中央寄せだった。下部ペインを縮めると縦が足りなくなり、
//   Inputs とスタートが画面外へ出て操作できない。一方で**横は余っている**（実測: 版面幅
//   1990px に対しフォームは 860px＝左右 57% が空白）。
//   縦に積むのをやめ、**設定（Tester Settings）と入力（Inputs＋実行）を横に分ける**のが
//   本 View の役割である。狭い版面では 1 列へ落として（横スクロールを作らない）縦積みへ戻す。
//
// 責務（SRP）: 「どこへ置くか」だけ。何を置くかは合成根が決め、面の中身は各 View が持つ。
//   本 View は面の実装を 1 つも import しない（DOM だけを知る）。
//
// CSS の所在: 寸法は `css/sim_run_form.css` が持つ（本 View は id と入れ子だけを作る）。
//   選択子は本ファイルが作る id の配下に閉じる＝結果ビューア（`?job=<id>`）へは 1 つも
//   当たらない（`tests/removed_ui_vocabulary_gate.test.js` が機械強制する）。

/** 版面の器の id（CSS の唯一の入口）。 */
export const SIM_RUN_FORM_ID = "simRunForm";
/** 左の列（設定）。Tester Settings と縮退面が入る。 */
export const SIM_RUN_FORM_SETTINGS_ID = "simRunFormSettings";
/** 右の列（入力と実行）。Inputs・スタート・掲示が入る。 */
export const SIM_RUN_FORM_INPUTS_ID = "simRunFormInputs";

/**
 * 2 列の版面（設定 / 入力）を生成・破棄する View を返す。
 *
 * @param {Document} doc 注入 DOM
 */
export function createSimRunLayoutView({ doc } = {}) {
  let root = null;
  let host = null;
  let settings = null;
  let inputs = null;

  return {
    isMounted() { return root !== null; },

    /** 器を `target` の下へ挿す（二重 mount は無視）。 */
    mount(target) {
      if (root) return root;
      host = target;
      root = doc.createElement("div");
      root.id = SIM_RUN_FORM_ID;
      settings = doc.createElement("div");
      settings.id = SIM_RUN_FORM_SETTINGS_ID;
      inputs = doc.createElement("div");
      inputs.id = SIM_RUN_FORM_INPUTS_ID;
      root.appendChild(settings);
      root.appendChild(inputs);
      host.appendChild(root);
      return root;
    },

    /** 設定（Tester Settings・縮退面）の挿し先。未 mount なら null。 */
    settingsHost() { return settings; },

    /** 入力と実行（Inputs・スタート・掲示）の挿し先。未 mount なら null。 */
    inputsHost() { return inputs; },

    /** 器を外す（文書へ何も残さない）。二重 unmount は無視。 */
    unmount() {
      if (root && host) host.removeChild(root);
      root = host = settings = inputs = null;
    },
  };
}
