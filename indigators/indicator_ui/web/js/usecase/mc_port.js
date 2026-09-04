// mc_port.js（usecase）— モンテカルロ実行の Output Boundary（ISSUE-368 スライス 5）。
//
// 設計入力: 設計書 §5 Output Boundary ／ §10 YAGNI 検証。
//   usecase は「誰が MC を回すか」を知らない。Worker で回すのか同期 fake で回すのかは
//   外側（adapter）の関心事であり、usecase は本契約しか見ない（DIP）。
//   この境界を YAGNI で消さないのは、実行場所の変更要因が**実在**するため
//   （ISSUE-362 GIL 律速・ISSUE-364 単一ワーカー詰まりで、サーバ実行が一度棄却されている）。
//
// 契約:
//   solve(spec, onProgress) -> Promise<EdgeResultDTO>
//     spec        : golden fixture と同じ snake_case の入力
//                   { win_rate, payoff_ratio, ruin_level, alpha, horizon, split_count, seed, sims }
//     onProgress  : (ratio: 0..1) => void（任意。実装は呼ばなくてよい）
//     失敗時       : McUnavailableError で reject する（無音で null を返さない）
//
// 依存ゼロ（domain も adapter も import しない）。

/**
 * MC を実行できない／実行に失敗したことを表す例外。
 *
 * 無音の縮退（結果 null・例外握り潰し）をしないための型。呼び出し側はこの型を見て
 * 「計算できなかった」ことを利用者へ告知する。原因（cause）は切り分けの入口として必ず残す。
 */
export class McUnavailableError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = 'McUnavailableError';
    if (options && 'cause' in options) {
      this.cause = options.cause;
    }
  }
}

/**
 * MonteCarloPort の契約を満たしているかを確かめ、満たしていれば port をそのまま返す。
 *
 * 契約違反はその場で投げる。黙って no-op の代役へ倒すと「計算するを押しても何も起きない」
 * という原因不明の不具合になり、配線ミスが本番まで生き残る。
 */
export function assertMonteCarloPort(port) {
  if (!port || typeof port !== 'object') {
    throw new TypeError('MonteCarloPort が注入されていません（solve を持つ実装が必要）');
  }
  if (typeof port.solve !== 'function') {
    throw new TypeError('MonteCarloPort の契約違反: solve(spec, onProgress) がありません');
  }
  return port;
}
