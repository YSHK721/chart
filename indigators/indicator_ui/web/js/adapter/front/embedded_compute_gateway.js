// EmbeddedComputeGateway（adapter/front/embedded_compute_gateway.js）— ComputeGateway 実装・A方式。
//
// 設計入力: 内部設計書 §7.1.1（ComputeGateway 契約）。
// A方式プロトタイプ: HTTP fetch せず、埋め込み事前計算データ SAMPLE_DATA.precomputed から
//   キー `${indicatorId}:${variant}` で系列群を返す。
// 契約（§7.1.1 事後条件）:
//   - result.ok === true なら series 非 null、result.generation === req.generation（レース制御）。
//   - キー不在は ComputeError を投げる（A方式の制限: 事前計算 variant のみ計算可能）。

export class ComputeError extends Error {
  constructor(message, { type = 'not_precomputed' } = {}) {
    super(message);
    this.name = 'ComputeError';
    this.type = type;
  }
}

export class EmbeddedComputeGateway {
  // source: { precomputed: { "<id>:<variant>": SeriesPayload[] } }（SAMPLE_DATA 相当）。
  constructor(source) {
    this._precomputed = (source && source.precomputed) || {};
  }

  // ComputeRequest -> ComputeResult。fetch しない（A方式）。
  async compute({ indicatorId, variant, generation = 0 } = {}) {
    const key = `${indicatorId}:${variant ?? 'default'}`;
    const series = this._precomputed[key];
    if (!series) {
      throw new ComputeError(`事前計算データ未収録のキー: ${key}（A方式は埋め込み variant のみ計算可能）`);
    }
    // generation はそのままエコー（§7.1.1: result.generation === req.generation）。
    return { ok: true, generation, series };
  }
}
