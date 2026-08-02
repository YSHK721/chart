// ComputeHttpClient（adapter/front/compute_http_client.js）— ComputeGateway 実装・B方式（fetch）。
//
// 設計入力: 内部設計書 §3.3.5（ComputeRequest→fetch POST→ComputeResult）、§6.3.1 リクエスト、
//   §6.3.2/6.3.3 正常応答（series）、§6.3.4 エラー応答（error.type/message）、§7.1.1 契約。
//
// 契約:
//   - POST /compute に JSON ボディ（indicatorId/variant/params/datasetRef）を送る。
//   - 200 応答なら ComputeResult { ok, generation, series } を返す（EmbeddedComputeGateway と
//     同一の戻り形。_gatewayAdapter が result.series を、recompute が result.generation を参照する）。
//   - 非200 応答は body.error（{type,message}）を読み ComputeError（error_type 保持）を throw。
//   - ネットワーク例外（fetch reject）は ComputeError（error_type='network'）へ翻訳して throw。
//
// fetch は注入可能（コンストラクタ）。DOM・実ネットワークに依存しない（テスト容易）。

// ComputeError は domain へ集約（単一定義）。本ファイルからは re-export し、呼び出し側
//   （import { ComputeHttpClient, ComputeError } ...）を破壊しない。error_type を保持する。
import { ComputeError } from '../../domain/compute_error.js';

export { ComputeError };

const COMPUTE_ENDPOINT = '/compute';

// ISSUE-157: /compute 応答タイムアウト（ms）。応答が返らない要求（接続ストール等）を放置すると
//   呼び出し側の coalesce ラッチ（_formingBusy）が永久に解放されず全指標更新が凍結するため、
//   一定時間で abort して ComputeError(network) に転換する（pending 機構が自動再試行する）。
//   実測の compute 最大 ~1s に対し十分な余裕（サーバ飽和時のキュー待ちも許容）。
const COMPUTE_TIMEOUT_MS = 30000;

export class ComputeHttpClient {
  // deps.fetch: 注入された fetch（既定はグローバル fetch）。
  // deps.timeoutMs: 応答タイムアウト（既定 30s・テストで短縮可能）。
  constructor({ fetch, timeoutMs = COMPUTE_TIMEOUT_MS } = {}) {
    this._fetch = fetch;
    this._timeoutMs = timeoutMs;
  }

  // ComputeRequest -> series（§7.1.1）。非200/ネットワーク例外は ComputeError へ翻訳。
  // generation はサーバがエコーし、recompute の競合採否（advanced.accepts(result.generation)）が
  // 参照する。転送しないと常に 0 がエコーされ recompute が破棄され params が反映されない。
  async compute({ indicatorId, variant, params, datasetRef, generation, timeframe, limit, mode, untilTime, forming, winStart, winEnd } = {}) {
    // timeframe（時間足）/ limit（直近 N 本）はサーバで resample・表示範囲制限に使う。
    // 省略時はサーバが原子（再集計なし）・全件として扱う（後方互換）。
    // mode（full/latest）は指定時のみ載せる（未指定はサーバ既定 full・後方互換でボディに含めない）。
    const reqBody = { indicatorId, variant, params, datasetRef, generation, timeframe, limit };
    if (mode !== undefined) {
      reqBody.mode = mode;
    }
    // [PROTO 再生 seam] untilTime（そのフレームの時点）。未指定は載せない＝ライブ（present）扱いで
    //   従来と完全同一ボディ（`!== undefined` gate＝present byte 挙動不変）。replay の reveal だけが送る。
    if (untilTime !== undefined) {
      reqBody.untilTime = untilTime;
    }
    // [PROTO 再生 seam] forming（足内更新中の形成中バー暫定 OHLC）。未指定は載せない＝確定足のまま計算。
    if (forming !== undefined) {
      reqBody.forming = forming;
    }
    // [ISSUE-238] 足内窓。forming の `to`（リプレイ現在時刻）と対で、サーバが「その時点までに
    //   到来した実 tick 数」を数えて形成中バーの volume にする（volume 未指定だと確定足の完成値が
    //   残り、足の先頭から未来の値を表示してしまう）。未指定は載せない＝ライブ扱い・従来ボディ不変。
    if (winStart !== undefined) {
      reqBody.winStart = winStart;
    }
    if (winEnd !== undefined) {
      reqBody.winEnd = winEnd;
    }
    const body = JSON.stringify(reqBody);

    // ISSUE-157: AbortController でタイムアウトを課す（signal は本文読み取りまで有効＝
    //   response.json() のストールも中断される）。AbortController 非提供環境（旧テスト等）は
    //   従来どおり無タイムアウト（後方互換）。
    const hasAbort = typeof AbortController === 'function';
    const aborter = hasAbort ? new AbortController() : null;
    const timerId = aborter ? setTimeout(() => aborter.abort(), this._timeoutMs) : null;

    let response;
    let payload;
    try {
      try {
        response = await this._fetch(COMPUTE_ENDPOINT, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body,
          ...(aborter ? { signal: aborter.signal } : {}),
        });
      } catch (cause) {
        // ネットワーク例外（タイムアウト・接続断等）を翻訳（§7.1.1 例外翻訳）。
        const reason = aborter && aborter.signal.aborted
          ? `応答タイムアウト（${this._timeoutMs}ms）`
          : cause.message;
        throw new ComputeError(`計算 API への接続に失敗しました: ${reason}`, { error_type: 'network' });
      }

      try {
        payload = await response.json();
      } catch (cause) {
        if (aborter && aborter.signal.aborted) {
          throw new ComputeError(`計算 API への接続に失敗しました: 応答タイムアウト（${this._timeoutMs}ms）`, { error_type: 'network' });
        }
        throw cause;
      }
    } finally {
      if (timerId !== null) {
        clearTimeout(timerId);
      }
    }

    if (!response.ok) {
      const error = (payload && payload.error) || {};
      throw new ComputeError(error.message || `計算 API エラー (HTTP ${response.status})`, {
        error_type: error.type || 'internal',
      });
    }

    // EmbeddedComputeGateway と同一の ComputeResult 形へ揃える（_gatewayAdapter が
    // result.series を、recompute が result.generation を参照するため。裸配列だと描画されない）。
    return {
      ok: payload.ok ?? true,
      generation: payload.generation ?? 0,
      series: payload.series,
    };
  }
}
