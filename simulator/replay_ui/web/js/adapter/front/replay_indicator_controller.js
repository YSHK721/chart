// replay_indicator_controller.js — reveal（因果リビール）対応の IndicatorController 派生。
//
// 設計入力: frontend_unification_design.md「indicator_controller (B) 双方向差分」段階移行 item5。
//   共有 present IndicatorController（../front/indicator_controller.js＝symlink 単一ソース）を extends し、
//   replay 固有の reveal 差だけを override/追加する（fork＝全文複製は禁止）。共有ベースは reveal 用の
//   inert seam（_extraComputeFields / forceTail / _untilTime gate 付き enterBar-union / onLiveTick typeof gate）
//   を持ち、present では byte 挙動不変（present テストで固定）。本 subclass はその seam を実装で埋める。
//
// reveal 差（present との違い）:
//   - untilTime（そのフレームの時点 T）: setUntilTime で設定し compute へ素通し（_extraComputeFields）。
//   - forming（足内更新の形成中バー）: recomputeFormingLatest が INTRABAR_FORMING_IDS の末尾点のみ
//     forceTail 差分再計算し compute へ素通しする。
//   - MP（Market Profile）: ReplayMarketProfileActor（共有 MarketProfileActor の subclass）駆動。/compute を
//     持たないため recomputeInstance は _recomputeMarketProfile へ委譲し、mode-aware で ticklive→enterBar /
//     normal・sessions・replay→refresh(as-of-T) に振り分ける。ticklive の成長自体は setupReplay
//     （render→enterBar / animateForming→feedTick）が actor を直接駆動する。_mpParams は基底の rich 実装
//     （mode/resmode/range/src/bins/va 全 param）を再利用し、全 4 モードを機能させる。

import { IndicatorController } from './indicator_controller.js';

// [reveal] 足内（ティック粒度）追従の対象指標。形成中バーを末尾へ差し込み、line/histogram の
//   最終点だけを updateSeriesTail で更新する（horizontal_line の水準線は据え置き）。移動平均に加え、
//   標準化窓を持たない profit_* 8 指標を対象とする（混在 kind は forceTail で末尾差分経路へ倒す）。
const INTRABAR_FORMING_IDS = new Set([
  'moving_averages',
  'profit_mfi', 'profit_rsi', 'profit_stc', 'profit_oscillator2',
  'profit_osi_ma', 'profit_hlband', 'profit_mfi_macd', 'profit_rsi_macd',
  // 標準化窓 W を持つ profit_* のうち、本体（line/histogram）を持つ 6 指標を追加（推奨A）。
  //   因果窓ゆえ過去点は repaint せず、最新点のみ forming で動く（実証済み）。profit_hl_band は
  //   horizontal_line のみ（アニメ可能な本体なし）のため対象外＝末尾差分では動かない。
  'profit_adx_needle', 'profit_arctan', 'profit_oscillator',
  'profit_rmm', 'profit_volatility', 'profit_rmm_macd',
]);

export class ReplayIndicatorController extends IndicatorController {
  constructor(opts) {
    super(opts);
    // [reveal] untilTime（再生のその時点・UNIX秒）。undefined=ライブ（present）。setUntilTime で設定し
    //   compute へ素通しする（_extraComputeFields）。present は本フィールドを持たないため seam は inert。
    this._untilTime = undefined;
    // [reveal] forming（足内更新中の形成中バー暫定 OHLC）。undefined=確定足のまま計算。
    this._forming = undefined;
  }

  // [reveal] untilTime を設定（以降の再計算がこの時点で計算される＝ライブ同一・df[:t+1]）。
  setUntilTime(t) {
    this._untilTime = t;
  }

  // [reveal] 形成中バー（暫定 OHLC）を設定。undefined で解除（確定足計算へ戻す）。
  setForming(forming) {
    this._forming = forming;
  }

  // 共有ベースの compute リクエスト seam を実装: untilTime/forming を素通しする。undefined は
  //   compute_http_client の `!== undefined` gate で不送信＝ライブ扱い（後方互換）。
  _extraComputeFields() {
    return { untilTime: this._untilTime, forming: this._forming };
  }

  // [reveal] 足内更新: 形成中バーを差し込み、対象指標（INTRABAR_FORMING_IDS）の末尾点のみ latest 差分
  //   再計算する。混在 kind（line+horizontal_line）でも forceTail=true で末尾差分経路へ倒し、line/histogram
  //   の最終点のみ更新（水準線は据え置き＝履歴潰れなし）。tgp 帯等の対象外は足確定値のまま（触らない）。
  //   forming 解除は finally で必ず行い、後続の確定足計算に forming を残さない。
  async recomputeFormingLatest(forming) {
    this.setForming(forming);
    try {
      for (const inst of [...this._state.applied]) {
        if (!INTRABAR_FORMING_IDS.has(inst.indicatorId)) {
          continue;                                        // 足内 latest 対象外（帯系等）は触らない
        }
        if (!this._meta.has(inst.instanceId)) {
          continue;
        }
        await this.recomputeInstance(
          inst.instanceId, null, this._paramsObject(inst.params), { mode: 'latest', forceTail: true },
        );
      }
    } finally {
      this.setForming(undefined);                          // 確定計算へ forming を残さない
    }
  }

  // MP アクターへ渡す取得 params。全モード機能化により全 param（mode/resmode/range/src/bins/va）を
  //   基底 _mpParams（present の rich 実装）で組み立てる。基底は _paramsObject 済みの平坦 params を受けるため、
  //   subclass の _paramsObject（配列/オブジェクト両受理）で正規化してから委譲する（mode-aware 駆動が有効化）。
  _mpParams(params) {
    return super._mpParams(this._paramsObject(params));
  }

  // MP 設定変更: /compute へ流さず setParams + 現在バー T（_untilTime）で再 enterBar（gear onApply 経路）。
  //   present は recomputeInstance で MP 分岐を持たない（present は _onGearMarketProfile 経路）ため、
  //   本 subclass で MP を enterBar 経路へ委譲し MP が /compute へ流出しないようにする。
  async recomputeInstance(instanceId, newVariant, newParams, opts = {}) {
    const meta = this._meta.get(instanceId);
    if (meta && this._isMarketProfile(meta.def)) {
      return this._recomputeMarketProfile(instanceId, newParams);
    }
    return super.recomputeInstance(instanceId, newVariant, newParams, opts);
  }

  // MP 設定変更（gear onApply）: /compute を呼ばず state.params を更新し、actor へ setParams +
  //   mode-aware で再駆動する。setParams が _applyMode で排他モードを遷移させた後、ticklive は push
  //   （enterBar で base 取り直し・現在バー T=_untilTime）、normal/sessions/replay は as-of refresh
  //   （getContext().to=T で as-seen-at-t 再取得）へ振り分ける。未注入時は state 更新のみ。
  async _recomputeMarketProfile(instanceId, newParams) {
    const meta = this._meta.get(instanceId);
    const params = newParams ?? this._defaultParams(meta.def);
    this._state = this._withParams(this._state, instanceId, params);
    if (this._marketProfile) {
      if (typeof this._marketProfile.setParams === 'function') {
        this._marketProfile.setParams(this._mpParams(params));
      }
      const isTicklive = typeof this._marketProfile.isTicklive === 'function'
        && this._marketProfile.isTicklive();
      if (isTicklive && this._untilTime != null
          && typeof this._marketProfile.enterBar === 'function') {
        await this._marketProfile.enterBar(this._untilTime); // ticklive: base 取り直し（push 系）。
      } else if (typeof this._marketProfile.refresh === 'function') {
        await this._marketProfile.refresh(); // normal/sessions/replay: as-of-T 再取得（因果）。
      }
    }
    this._persistAll();
    this._renderLegend();
    return true;
  }

  // UC-04 表示/非表示。MP は renderer を持たず actor.setEnabled(visible) へ委譲する（present の MP 専用
  //   ハンドラ _toggleMarketProfileVisible を再利用）。非 MP は共有ベースへ委譲する。
  toggleVisible(instanceId) {
    const inst = this._state.applied.find((i) => i.instanceId === instanceId);
    const def = inst ? this._catalog.get(inst.indicatorId) : null;
    if (inst && this._isMarketProfile(def)) {
      return this._toggleMarketProfileVisible(inst);
    }
    return super.toggleVisible(instanceId);
  }

  // UC-05 削除。MP は renderer.remove を持たず actor.setEnabled(false)+detach（あれば）へ委譲する
  //   （present の MP 専用ハンドラ _removeMarketProfile を再利用）。非 MP は共有ベースへ委譲する。
  removeInstance(instanceId) {
    const inst = this._state.applied.find((i) => i.instanceId === instanceId);
    const def = inst ? this._catalog.get(inst.indicatorId) : null;
    if (inst && this._isMarketProfile(def)) {
      return this._removeMarketProfile(inst);
    }
    return super.removeInstance(instanceId);
  }
}
