// forming_seq_client.js — 足内一括計算（ISSUE-232）の HTTP クライアント（リプレイ専用）。
//
// POST /compute { mode:'latest_seq', formingSeq:[...] } → { ok, generation, steps:[series,...] }。
// steps[i] は mode:'latest' 単発の series と完全同値（サーバ側 causal_compute_seq のゲートで固定）。
//
// 共有の ComputeHttpClient（symlink＝ライブと同一実体）には手を入れない。本機能はリプレイ専用で
// あり、共有クライアントへ新モードを足すとライブの依存面が広がるため、専用の薄いクライアントを
// 別実体で持つ（ライブへの影響ゼロ）。

const COMPUTE_ENDPOINT = '/compute';
// 一括計算はステップ数ぶん重い（実測: MA 20 ステップ 177ms / 重い指標は数秒）。先読みが
//   間に合わない場合は呼び出し側が従来経路へ落とすため、ここでは長めの上限だけを課す。
const SEQ_TIMEOUT_MS = 30000;

export class FormingSeqClient {
  constructor({ fetch, timeoutMs = SEQ_TIMEOUT_MS } = {}) {
    this._fetch = fetch;
    this._timeoutMs = timeoutMs;
  }

  // steps（series の配列・formingSeq と同順）を返す。失敗は例外（呼び出し側がフォールバック）。
  async computeSeq({
    indicatorId, variant, params, datasetRef, timeframe, limit, untilTime, formingSeq,
  } = {}) {
    const body = JSON.stringify({
      indicatorId, variant, params, datasetRef, timeframe, limit, untilTime,
      mode: 'latest_seq', formingSeq, generation: 0,
    });
    const hasAbort = typeof AbortController === 'function';
    const aborter = hasAbort ? new AbortController() : null;
    const timerId = aborter ? setTimeout(() => aborter.abort(), this._timeoutMs) : null;
    try {
      const response = await this._fetch(COMPUTE_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        ...(aborter ? { signal: aborter.signal } : {}),
      });
      const payload = await response.json();
      if (!response.ok || !payload || payload.ok === false) {
        const msg = payload && payload.error ? payload.error.message : `HTTP ${response.status}`;
        throw new Error(`足内一括計算に失敗: ${msg}`);
      }
      return Array.isArray(payload.steps) ? payload.steps : [];
    } finally {
      if (timerId != null) {
        clearTimeout(timerId);
      }
    }
  }
}
