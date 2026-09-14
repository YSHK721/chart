// sheet_presenter（usecase/sheet_presenter.js）— 応答 1 件を受けて「何を描き直すか」を決める。
//
// 持っているもの（省リソース段階 1・2、依頼者承認 2026-08-30）:
//   - サーバの状態トークン（`known_state` に載せ、素材不変ならサーバは unchanged の極小応答を返す）
//   - 直近の完全応答（unchanged のときチャートの差分再描画へ使う材料）
//   - 描画鍵（内容が直前と同一なら表を作り直さない＝毎秒の全再構築を避ける）
//   - 版面がいま「成功した応答」を映しているかの札
//
// なぜ合成根から出したのか（ISSUE-502 段階 4C・F-3）:
//   合成根は「結線だけ」を名乗りながら、上の 4 つの**方針**を保持していた。方針は
//   「いつ描き直すか」の判断であり、「誰と誰を繋ぐか」とは変更要求元が別である
//   （省リソースの段階が増えるたびに合成根が書き換わる状態だった）。
//   View も HTTP も知らない形にしてあるので、判断だけを単体で固定できる。
//
// 計算量（絶対命令 §4.1）: 応答 1 件につき鍵を 1 回だけ組む。鍵が同じなら表の再構築を
//   発行しない——「作ってから捨てる」再構築が毎秒起きるのを防ぐのが本モジュールの目的である。

/**
 * 描画の判断器を作る。
 *
 * @param {object}   opts
 * @param {Function} opts.dayStampOf 日付印を返す（日替わりで「今日/昨日」表記を確実に描き直す）
 * @returns {object}
 */
export function createSheetPresenter({ dayStampOf }) {
  /** サーバの状態トークン（省リソース段階 2）。 */
  let stateToken = null;
  /** 直近の完全応答（unchanged 時のチャート差分再描画＝アンカー再試行の材料）。 */
  let lastFull = null;
  /** 直近に描いた内容の鍵。 */
  let lastKey = null;
  /** 直近に**描いた**応答が成功だったか（借用の着弾が失敗掲示を上書きしないための札）。 */
  let presentedOk = false;
  /** 借用プロファイルの世代（描画鍵に混ぜ、借用が版面へ確実に反映されるようにする）。 */
  let generation = 0;

  /**
   * 描画済みの内容を表す鍵。
   *
   * 日付印を含める＝到達時刻の「今日/昨日」表記が日替わりで確実に描き直される。
   * 借用 MP の世代も含める——プロファイルが入れ替わったのに応答が同一だと、鍵が変わらず
   * MP 列だけが古いまま残る（借りたのに描かない＝作って捨てる計算になる）。
   */
  function keyOf(response) {
    return `${dayStampOf()}|${generation}|${JSON.stringify(response)}`;
  }

  return {
    /** 器を出し直したときの初期化（unchanged では空の版面が残るので完全応答を要求する）。 */
    reset() {
      stateToken = null;
      lastFull = null;
      lastKey = null;
      presentedOk = false;
    },

    /** 次要求の `known_state` に載せるトークン。 */
    stateToken() {
      return stateToken;
    },

    /**
     * 応答 1 件を受け、何をどの応答で描くかを返す。
     *
     * `hasKeyed` / `hasAlways` を真偽で別に返すのは、「描かない」と「空の応答を描く」を
     * 取り違えないためである（応答そのものが欠けている経路でも判断は一意に決まる）。
     *
     * @param {object} response `/reach_sheet` の応答
     * @returns {{full: ?object, keyed: *, hasKeyed: boolean, always: *, hasAlways: boolean}}
     *   - `full`   … 完全応答（tails の申告を組み直す材料）。unchanged / 失敗では null
     *   - `keyed`  … 内容が変わったときだけ描き直す版面へ渡す応答
     *   - `always` … 差分適用で毎回受け取る版面へ渡す応答
     */
    accept(response) {
      // 省リソース段階 2: unchanged＝素材不変。トークンだけ受け取り、内容の描き直しはしない
      //   （チャートの差分描画のみ通す＝右端余白アンカーの再試行が枯れないように）。
      if (response && response.ok === true && response.unchanged === true) {
        if (typeof response.state === 'string') {
          stateToken = response.state;
        }
        return {
          full: null,
          keyed: null,
          hasKeyed: false,
          always: lastFull,
          hasAlways: lastFull !== null,
        };
      }
      let full = null;
      if (response && response.ok === true) {
        if (typeof response.state === 'string') {
          stateToken = response.state;
        }
        lastFull = response;
        full = response;
      }
      presentedOk = !!(response && response.ok === true);
      // 省リソース段階 1: 内容が直前の描画と同一なら表を作り直さない
      //   （毎秒の全再構築は内容不変時にはまるごと浪費・依頼者指摘 2026-08-30）。
      const key = keyOf(response);
      const hasKeyed = key !== lastKey;
      if (hasKeyed) {
        lastKey = key;
      }
      return {
        full, keyed: response, hasKeyed, always: response, hasAlways: true,
      };
    },

    /**
     * 借用プロファイルが入れ替わったことを記録する（描画鍵の世代を進める）。
     *
     * 借りたら**必ず 1 回描く**ために鍵を変える。応答が unchanged だと `accept` は版面を
     * 触らないため、世代を進めないと借用が版面に出ないまま捨てられる。
     */
    bumpGeneration() {
      generation += 1;
    },

    /**
     * いま描き戻してよい応答（無ければ null）。
     *
     * **版面が失敗を掲示している間は描き戻さない**。`lastFull` は直近の *成功* 応答なので、
     * 無条件に描くとシートが落ちている最中に古い行が復活し、理由の掲示も消える——
     * ユーザーには復旧したように見える（最も危険な縮退）。合成根は MP をシートより先に
     * 発行するため、この順序（失敗掲示 → 借用の着弾）は実際に起こる。
     */
    repaintTarget() {
      return lastFull && presentedOk ? lastFull : null;
    },

    /** いま描いた内容へ鍵を揃える（直後の同一応答で二重に描き直さない）。 */
    syncKey(response) {
      lastKey = keyOf(response);
    },
  };
}
