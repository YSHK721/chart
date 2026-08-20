// position_sizing_plan.js（usecase）— Step 2（採用 f の選択）＋ Step 3（ロット変換）の合成
//   と ViewModel 生成（ISSUE-368 スライス 5）。
//
// 設計入力: 設計書 §3 UC-01/UC-03 ／ §5 Input Boundary。
//
// 責務: **合成だけ**を行う。式は 1 つも持たない。
//   - Step 1（エッジと破産確率）は MonteCarloPort 越し（Worker か fake かを知らない＝DIP）
//   - 採用 f の選択（Step 2）は参照実装 :580 chosenF() と同一
//   - Step 3 は domain/split_entry_plan.js（Python 権威の鏡）へ丸ごと委譲
//   - 水準の不変条件は domain/price_levels.js（E-02）へ委譲
//   表示文字列は作らない（Presenter の責務・§3 UC-04）。ViewModel は構造化データだけを持つ。
//
// 参照実装との対応:
//   :580      chosenF()（full は max(f*,0)・half・safe）
//   :643      S.fFull / S.fHalf / S.fSafe は runMC が入るまで 0 → 計算前は f=0（:1092）
//   :585-592  派生カード（q / EV / f* / ハーフ）は MC 非依存で即時表示
//
// import は domain と同一層（usecase）のみ。

import { kellyFraction } from '../domain/edge_ruin_core.js';
import { buildSplitEntryPlan } from '../domain/split_entry_plan.js';
import { assertMonteCarloPort } from './mc_port.js';

// 採用 f の 3 択（参照実装 :339-343 のセグメント）。
export const FRACTION_CHOICES = ['safe', 'half', 'full'];

export class PositionSizingPlanUseCase {
  constructor({ mcPort, levels, params }) {
    this._port = assertMonteCarloPort(mcPort);
    this._levels = levels;
    this._params = { ...params };
    this._edge = null;         // Step 1 の結果（MC 実行まで null＝参照実装の S.f* が 0 の状態）
  }

  // ---- Input Boundary ----

  setLevels(levels) {
    this._levels = levels;
    return this.viewModel();
  }

  setParams(patch) {
    this._params = { ...this._params, ...patch };
    return this.viewModel();
  }

  // MC を実行して採用 f を確定させる。失敗は McUnavailableError のまま伝える
  //   （握り潰して null を返すと「押しても何も起きない」になる）。
  async runMonteCarlo(onProgress = () => {}) {
    const edge = await this._port.solve(this._edgeSpec(), onProgress);
    this._edge = edge;
    return this.viewModel();
  }

  // ---- ViewModel ----

  viewModel() {
    const fraction = this._chosenFraction();
    const plan = this._plan(fraction);
    return {
      derived: this._derived(),
      edge: this._edge,
      fraction,
      fractionChoice: this._params.fractionChoice,
      plan,
      violations: this._levels.validate(),
      levelLines: {
        direction: this._levels.direction,
        entryPrices: this._levels.entryPrices,
        stopPrice: this._levels.stopPrice,
        takePrice: this._levels.takePrice,
        // 建玉が 0 のときロスカット価格は意味を持たない（到達する価格が無い）。
        losscutPrice: plan.total_lot > 0 ? plan.losscut_price : null,
      },
    };
  }

  // ---- 内部 ----

  // Step 1 の入力（golden fixture と同じ snake_case で port へ渡す）。
  _edgeSpec() {
    const p = this._params;
    return {
      win_rate: p.winRate,
      payoff_ratio: p.payoffRatio,
      ruin_level: p.ruinLevel,
      alpha: p.alpha,
      horizon: p.horizon,
      split_count: p.splitCount,
      seed: p.seed,
      sims: p.sims,
    };
  }

  // :585-592 の派生カード（MC 非依存＝入力を変えた瞬間に出る）。
  _derived() {
    const p = this._params;
    const q = 1 - p.winRate;
    const kelly = kellyFraction(p.winRate, p.payoffRatio);
    return {
      lossRate: q,
      expectedValue: p.payoffRatio * p.winRate - q,
      kellyFraction: kelly,
      halfKellyFraction: Math.max(kelly, 0) / 2,
    };
  }

  // :580 chosenF()。MC 未実行（edge==null）は参照実装の S.f*=0 と同じく 0。
  _chosenFraction() {
    const edge = this._edge;
    if (!edge) {
      return 0;
    }
    if (this._params.fractionChoice === 'full') {
      return Math.max(edge.kellyFraction, 0);
    }
    if (this._params.fractionChoice === 'half') {
      return edge.halfKellyFraction;
    }
    return edge.constrainedFraction;
  }

  // Step 3 は domain へ丸ごと委譲する（距離・重み・ロット・証拠金・建て制約の式は持たない）。
  _plan(fraction) {
    const p = this._params;
    return buildSplitEntryPlan({
      direction: this._levels.direction,
      entry_prices: [...this._levels.entryPrices],
      stop_price: this._levels.stopPrice,
      take_price: this._levels.takePrice,
      fraction,
      balance: p.balance,
      point_value: p.pointValue,
      margin_rate: p.marginRate,
      win_rate: p.winRate,
      weight_pattern: p.weightPattern,
      custom_weights: p.customWeights ?? null,
      lot_mode: p.lotMode,
      cap_basis: p.capBasis,
    });
  }
}
