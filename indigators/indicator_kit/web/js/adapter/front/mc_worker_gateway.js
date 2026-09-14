// mc_worker_gateway.js — MonteCarloPort の Web Worker 実装（ISSUE-368 スライス 5）。
//
// 設計入力: 設計書 §6「Adapter: McWorkerGateway」／§7（並列実行＝Web Worker・ESM）。
//
// なぜ Worker か: MC は約 6000 万ループでサーバ送りにできない（ISSUE-362 GIL 律速・
//   ISSUE-364 単一ワーカー詰まりで一度棄却済み）。メインスレッドで回すとチャート操作が固まる。
//
// 例外翻訳: Worker 非対応・生成失敗・worker の error・worker からの error メッセージを
//   すべて **McUnavailableError** へ翻訳する（設計書 §6「無音の縮退をしない」）。原因は
//   cause に必ず残す＝切り分けの入口を消さない。
//
// 1 回の solve につき worker を 1 つ作り、決着したら必ず terminate する。
//   使い回すと「前回の実行の結果が今回の Promise を解決する」という取り違えが起きうるが、
//   毎回作れば取り違えの余地が構造的に無い（MC は「計算する」を押したときだけ走る）。
//
// Worker URL は `new Worker(new URL('./position_sizing_mc_worker.js', import.meta.url), ...)` の
//   形で書く。この形は `tests/worker_url_resolution.test.js` が走査し、配信ルート配下に
//   対象が実在することを検定する（`served_import_resolution.test.js` の正規表現は
//   Worker URL を拾わないため＝実測）。

import { McUnavailableError } from '../../usecase/mc_port.js';

// 既定の Worker 生成。Worker 非対応の実行環境（SSR・node のテスト）ではここで失敗させ、
//   呼び出し側には McUnavailableError として届ける（黙って同期実行へ倒さない＝UI が固まる）。
function createDefaultWorker() {
  if (typeof Worker === 'undefined') {
    throw new Error('この実行環境は Web Worker に対応していません');
  }
  return new Worker(new URL('./position_sizing_mc_worker.js', import.meta.url), { type: 'module' });
}

export class McWorkerGateway {
  constructor({ createWorker = createDefaultWorker } = {}) {
    this._createWorker = createWorker;
  }

  // MonteCarloPort.solve。spec は golden fixture と同じ snake_case のまま worker へ渡す。
  solve(spec, onProgress = null) {
    return new Promise((resolve, reject) => {
      let worker;
      try {
        worker = this._createWorker();
      } catch (cause) {
        reject(new McUnavailableError(
          `モンテカルロ実行を開始できません: ${cause && cause.message ? cause.message : cause}`,
          { cause },
        ));
        return;
      }

      let settled = false;
      const finish = (fn, value) => {
        if (settled) {
          return;   // 決着後に届いた遅延メッセージで二重解決しない。
        }
        settled = true;
        worker.removeEventListener('message', onMessage);
        worker.removeEventListener('error', onError);
        if (typeof worker.terminate === 'function') {
          worker.terminate();
        }
        fn(value);
      };

      function onMessage(event) {
        const data = event && event.data;
        if (!data || typeof data.type !== 'string') {
          return;   // 未知の形は無視（解決も棄却もしない）。
        }
        if (data.type === 'progress') {
          if (typeof onProgress === 'function') {
            onProgress(data.ratio);
          }
          return;
        }
        if (data.type === 'result') {
          finish(resolve, data.result);
          return;
        }
        if (data.type === 'error') {
          finish(reject, new McUnavailableError(
            `モンテカルロ実行が失敗しました: ${data.message}`,
          ));
        }
      }

      function onError(event) {
        finish(reject, new McUnavailableError(
          `モンテカルロ実行が失敗しました: ${event && event.message ? event.message : '不明なエラー'}`,
          { cause: event },
        ));
      }

      worker.addEventListener('message', onMessage);
      worker.addEventListener('error', onError);
      worker.postMessage({ type: 'solve', spec });
    });
  }
}
