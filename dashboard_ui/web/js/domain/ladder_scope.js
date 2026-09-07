// ladder_scope（domain/ladder_scope.js）— 第 1 表の「どの行を出すか」の状態機械。
//
// 設計入力（依頼者指示 2026-08-30「切り替えできるようにしろ。短期・中期・長期・オール」→
//   「オール」は「全期間」へ改称 →「期間も含めて、時間足も**複数選択**できるように」）:
//   期間ボタンはそのグループの時間足をまとめてトグルし、時間足ピルと**同一の選択集合**を
//   操作する（別のフィルタ軸を作らない）。全期間は窓なし全量のモード。
//
//   複数選択の区分として意味を持つよう、§4.3 の閾値（1h・1D）で**互いに素な時間足の帯**に
//   区切る（短期＝1h 未満 / 中期＝1h 以上 1D 未満 / 長期＝1D 以上）。§4.3 の**地平**
//   （行の「次のターゲット」印）は別概念のまま変えない——地平は累積の候補集合、こちらは
//   表示フィルタの区分である。
//
// なぜ View から出したのか（ISSUE-502 段階 4C・F-2）:
//   選択の遷移（どのボタンが何を反転させるか・全期間が選択集合をどう戻すか）は**状態機械**
//   であって版面ではない。View に置くと遷移の検証にボタンの DOM を組む必要があり、
//   「押す人」（依頼者の操作仕様）と「描く人」（列・セルの構造）という別アクターの変更要求が
//   同じモジュールへ集まる（SRP）。見た目（押下状態のクラス・トーン）は View に残し、
//   ここは真偽だけを答える。
//
// 切替は**描き直すだけ**で発行を生まない（発行判定は sheet_poller の唯一責務のまま）。
//
// 計算量: filter は行数に比例（1 巡）。走査も発行もしない。

import { DASHBOARD_TIMEFRAMES } from './dashboard_timeframes_generated.js';

/** 期間グループ。中身は表示時間足の唯一源から切り出す（写しを持たない）。 */
export function buildGroups(timeframes) {
  const mediumAt = timeframes.indexOf('1h');
  const longAt = timeframes.indexOf('1D');
  return Object.freeze([
    { key: 'short', label: '短期', tfs: timeframes.slice(0, mediumAt) },
    { key: 'medium', label: '中期', tfs: timeframes.slice(mediumAt, longAt) },
    { key: 'long', label: '長期', tfs: timeframes.slice(longAt) },
  ]);
}

/**
 * 表示範囲の状態機械を作る。
 *
 * @param {object} [opts]
 * @param {readonly string[]} [opts.timeframes] 表示時間足（短い順・唯一源の射影）
 */
export function createLadderScope({ timeframes = DASHBOARD_TIMEFRAMES } = {}) {
  const groups = buildGroups(timeframes);
  /** 選択中の時間足（唯一の選択状態。期間ボタンも時間足ピルもこの集合を操作する）。
   *  既定は全選択。全選択のときはフィルタ自体を通さない＝未知の時間足の行も従来どおり出る。 */
  let selected = new Set(timeframes);
  /** 全期間（窓なし全量）モード。選択を操作した瞬間に解除される。 */
  let windowless = false;

  /** 絞り込みが効いているか（全選択・全期間ではフィルタを通さない）。
   *  `this` に依らない素の関数として持つ——呼び手がメソッドを取り外して渡しても壊れない。 */
  const narrowed = () => !windowless && selected.size !== timeframes.length;

  return {
    /** 期間グループ（版面のボタンを組む側が読む）。 */
    groups,

    /** その時間足が選択中か。 */
    isOn(timeframe) {
      return selected.has(timeframe);
    },

    /** そのグループが「まるごと選択中」か（期間ボタンの押下状態）。 */
    isGroupActive(key) {
      const group = groups.find((g) => g.key === key);
      return group ? group.tfs.every((tf) => selected.has(tf)) : false;
    },

    /** 全期間（窓なし）モードか。 */
    isWindowless() {
      return windowless;
    },

    /** 時間足 1 本の反転（窓なしは解除される）。 */
    toggleTimeframe(timeframe) {
      windowless = false;
      if (selected.has(timeframe)) selected.delete(timeframe);
      else selected.add(timeframe);
    },

    /** グループまるごとの反転（全部入っていれば全部外す・窓なしは解除される）。 */
    toggleGroup(key) {
      windowless = false;
      const group = groups.find((g) => g.key === key);
      if (!group) {
        return;
      }
      const allOn = group.tfs.every((tf) => selected.has(tf));
      for (const tf of group.tfs) {
        if (allOn) selected.delete(tf); else selected.add(tf);
      }
    },

    /** 全期間の反転。入るときは全選択へ戻す（もう一度押すと窓ありへ戻る・選択は全選択のまま）。 */
    toggleWindowless() {
      windowless = !windowless;
      if (windowless) selected = new Set(timeframes);
    },

    /** 絞り込みが効いているか（全選択・全期間ではフィルタを通さない）。 */
    isNarrowed: narrowed,

    /**
     * 表示する行。並びはサーバのまま（順序を再計算しない）。
     *
     * @param {Array<object>} rows 応答の全行
     */
    filter(rows) {
      return narrowed()
        ? rows.filter((row) => selected.has(String(row.timeframe)))
        : rows;
    },

    /**
     * 現在値行が入る位置。
     *
     * 全量ではサーバの `current_index` が唯一源（範囲外の指定は端へ倒す）。絞った範囲では
     * 行が抜けるため、同じ定義（現在値より上＝距離が正の行数）で**数え直す**
     * （数値の再計算ではない。距離の符号はサーバの値そのもの）。
     *
     * @param {Array<object>} rows          絞り込み後の行
     * @param {*}             serverIndex   応答の `current_index`
     */
    currentIndexOf(rows, serverIndex) {
      return narrowed()
        ? rows.filter((row) => Number(row.distance) >= 0).length
        : Math.max(0, Math.min(Number(serverIndex) || 0, rows.length));
    },
  };
}
