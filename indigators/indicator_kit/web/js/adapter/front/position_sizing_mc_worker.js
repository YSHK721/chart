// position_sizing_mc_worker.js — モンテカルロ実行 Worker の本体（ISSUE-368 スライス 5）。
//
// 設計入力: 設計書 §7（並列実行＝Web Worker・ESM）／§8 依存方向図（worker → domain のみ）。
//
// 依存規約（構造ガード `tests/worker_url_resolution.test.js` が施行する）:
//   **domain しか import しない**。DOM・lightweight-charts・fetch を触らない。
//   Worker には window も document も無く、触れば実行時に落ちる。node の単体検定でも
//   落ちないようにするため、`self` への配線は存在確認の内側にだけ置く
//   （モジュールの読み込み自体は副作用を持たない）。
//
// メッセージ契約（mc_worker_gateway.js と対）:
//   受信 { type:'solve', spec }
//   送信 { type:'progress', ratio } … 0<ratio≤1
//        { type:'result',   result } … EdgeResultDTO
//        { type:'error',    message } … 計算中の例外（Worker を黙って死なせない）

import { solveEdgeRuin } from '../../domain/edge_ruin_core.js';

/**
 * 受信メッセージ 1 通を処理して post で投げ返す（Worker の外でも検定できる純粋な glue）。
 *
 * 例外を外へ投げない: Worker の中で投げると 'error' イベントとしてしか観測できず、
 * どの入力で落ちたのかが失われる。error メッセージへ翻訳して返す。
 */
export function handleWorkerMessage(data, post) {
  if (!data || data.type !== 'solve') {
    return;   // 未知の種別・空メッセージは無視（勝手に計算を始めない）。
  }
  try {
    const result = solveEdgeRuin(data.spec, (ratio) => post({ type: 'progress', ratio }));
    post({ type: 'result', result });
  } catch (err) {
    post({ type: 'error', message: err && err.message ? err.message : String(err) });
  }
}

// Worker として読み込まれたときだけ配線する（node の単体検定では self が無いので何もしない）。
if (typeof self !== 'undefined' && typeof self.addEventListener === 'function') {
  self.addEventListener('message', (event) => {
    handleWorkerMessage(event && event.data, (msg) => self.postMessage(msg));
  });
}
