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
    winStart = null, winEnd = null,
  } = {}) {
    // ISSUE-238: 足内窓（winStart/winEnd）を添える。サーバは各 formingSeq 要素の `to` と
    //   この窓から「その時点までに到来した実 tick 数」を数え、形成中バーの volume にする。
    //   窓の規則源はフロントの intrabarWindow 1 箇所（セッション日・右ラベル規約をサーバへ写さない）。
    const body = JSON.stringify({
      indicatorId, variant, params, datasetRef, timeframe, limit, untilTime,
      mode: 'latest_seq', formingSeq, generation: 0,
      ...(winStart != null && winEnd != null ? { winStart, winEnd } : {}),
    });
    const hasAbort = typeof AbortController === 'function';
    const aborter = hasAbort ? new AbortController() : null;
    const timerId = aborter ? setTimeout(() => aborter.abort(), this._timeoutMs) : null;
    try {
      // ISSUE-233（実 UI 実測で確定した不具合）: `this._fetch(...)` はレシーバ付き呼出になり、
      //   注入されたのがブラウザの素の `fetch`（replay.js の既定値 `fetchImpl = fetch`）のとき
      //   "Failed to execute 'fetch' on 'Window': Illegal invocation" で必ず失敗する。
      //   呼び出し側は失敗を握り潰して従来経路へ落とすため（replay.js の .catch(() => null)）、
      //   **足内一括計算が 1 度も成立していなかった**（実 UI 実測: 指標更新回数 0）。
      //   関数参照として呼び（this=undefined）、束縛の有無に依らず動くようにする。
      const doFetch = this._fetch;
      const response = await doFetch(COMPUTE_ENDPOINT, {
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
