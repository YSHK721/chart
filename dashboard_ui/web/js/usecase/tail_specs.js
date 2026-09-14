// tail_specs（usecase/tail_specs.js）— なめらか再生で「どの instance の末尾値を申告するか」と
//   「返ってきた末尾値をどのセルへ流すか」の台帳。
//
// 設計入力（依頼者指示 2026-08-31: 第 1・第 2 表もライブチャートと同じ更新粒度）:
//   `/live_ticks` は tick 適用と同一同期ブロックで instance ごとの末尾値（サーバ計算）を返す。
//   申告するのは**表に出ている instance だけ**である——表に出ない instance へ tails を計算させると
//   使わない計算を発行することになる（絶対命令 §4.1）。
//
// なぜ合成根から出したのか（ISSUE-502 段階 4C・F-3）:
//   合成根は「結線だけ」を名乗りながら、`params_key` の復元・計算足の載せ方（ISSUE-274 の
//   MTF override 規約）・申告 ID の合成という**契約の知識**を保持していた。これは live_ticks の
//   契約が変わったときに書き換わる場所であり、結線（誰と誰を繋ぐか）とは変更要求元が別である。
//
// 申告 ID の合成は**ここ 1 箇所**（specs の申告と tails の引き当てが同じ関数を通る＝ずれない）。
//
// 計算量: rebuild はセル数＋行数に比例（各 1 巡）。重複 instance は 1 本にまとめるので、
//   同じ末尾値を複数回計算させない。発行そのものは呼び手（LiveTickPlayer）が決める。

/**
 * instance_key（4 要素）→ tails の申告 ID。
 *
 * @param {readonly string[]} key instance_key
 * @returns {string}
 */
export function tailInstanceIdOf(key) {
  // 区切りは NUL（instance_key の要素の中に現れない文字）。可視文字で繋ぐと、要素側に同じ
  //   文字が来たとき別の instance が同じ ID へ潰れる（潰れても値は出るので出力の検査では
  //   落ちない種類のずれである）。
  return key.join('\u0000');
}

/**
 * instance_key から `/live_ticks` の spec を 1 本組む（不能なら null）。
 *
 * @param {*} key instance_key（4 要素: indicatorId / variant / params_key / timeframe）
 * @returns {?{instanceId: string, indicatorId: string, variant: string, params: object}}
 */
export function tailSpecOf(key) {
  if (!Array.isArray(key) || key.length !== 4) {
    return null;
  }
  let params;
  try {
    params = JSON.parse(key[2]);   // params_key は json.dumps＝JSON として復元できる（契約）。
  } catch {
    return null;
  }
  if (!params || typeof params !== 'object') {
    return null;
  }
  return {
    instanceId: tailInstanceIdOf(key),
    indicatorId: key[0],
    variant: key[1],
    // 計算足は instance の軸（ISSUE-274 の MTF override と同じ規約で params.timeframe に載せる）。
    params: { ...params, timeframe: key[3] },
  };
}

/**
 * 申告と流し先の台帳を作る。
 *
 * @returns {{rebuild: Function, specs: Function, targetOf: Function, clear: Function}}
 */
export function createTailSpecLedger() {
  /** `/live_ticks` へ申告する spec（唯一源＝完全応答）。 */
  let specs = [];
  /** instanceId → 第 2 表の流し先（行列と系列名）。 */
  const targets = new Map();

  return {
    /**
     * 完全応答（cells ＋ rows）から申告と流し先を組み直す。
     *
     * @param {object} response `/reach_sheet` の完全応答
     */
    rebuild(response) {
      specs = [];
      targets.clear();
      const declared = new Set();
      const declare = (key) => {
        const spec = tailSpecOf(key);
        if (!spec || declared.has(spec.instanceId)) {
          return spec ? spec.instanceId : null;
        }
        declared.add(spec.instanceId);
        specs.push(spec);
        return spec.instanceId;
      };
      for (const cell of (Array.isArray(response.cells) ? response.cells : [])) {
        if (!cell || !cell.value_series) {
          continue;   // 宣言の無いセル（旧応答・積算セル等）は流さない＝従来の 1s 表示のまま。
        }
        const instanceId = declare(cell.instance_key);
        if (instanceId !== null && !targets.has(instanceId)) {
          targets.set(instanceId, {
            indicatorId: cell.indicator_id,
            timeframe: cell.timeframe,
            valueSeries: cell.value_series,
          });
        }
      }
      // 第 1 表（依頼者指示 2026-08-31: 距離・価格・差もライブチャート粒度）。行の instance を
      //   申告する。流し先の解決は View 側（instance_key × series → 行）なのでここでは申告のみ。
      for (const row of (Array.isArray(response.rows) ? response.rows : [])) {
        if (row && typeof row.series === 'string' && row.series) {
          declare(row.instance_key);
        }
      }
    },

    /** いま申告している spec の並び。 */
    specs() {
      return specs;
    },

    /** 申告 ID に対応する第 2 表の流し先（無ければ undefined）。 */
    targetOf(instanceId) {
      return targets.get(instanceId);
    },

    /** 申告ごと捨てる（器を出し直すとき）。 */
    clear() {
      specs = [];
      targets.clear();
    },
  };
}
