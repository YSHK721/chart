// MpLiveModeCoordinator（adapter/front/mp_live_mode_coordinator.js）— ライブトグル状態と MP 表示モードの連動役。
//   present（B方式）固有。replay へは配線しない（replay は LiveFollowController 非所持）。
//
// 確定仕様（present オーケストレーション・MP actor は不変）:
//   - チャート FOLLOW（ライブトグル ON・右端追従）→ MP 実効モードを liveMode（'ticklive'＝足内成長）にする。
//   - チャート ANALYSIS（分析・過去へパン/トグル OFF）→ MP 実効モードを「ユーザーが gear で選んだ記憶モード」
//     _mpUserMode（normal/replay/sessions）へ戻す。未選択時は defaultMode（catalog 既定＝'normal'）。
//   ＝手動で Tickライブ を選ぶ必要が消え、gear 選択は「分析モード時の表示」として記憶される。
//
// 配線（composition_root_front で注入）:
//   - resolve(userMode): IndicatorController の MP param 構築（mpModeResolver）から呼ばれる。
//       userMode を記憶し、現在のライブ状態に応じた実効モードを返す。副作用は記憶のみ（actor へは触れない）。
//   - onLiveStateChange(isFollow): LiveFollowController の遷移フック（_applyFollow/_applyAnalysis）。
//       状態を更新し、reapply（controller.reapplyMarketProfileMode の注入）で実効モードを再適用させる。
//   - reapply: 現在表示中 MP へ実効モードを再適用する副作用（controller 経由・MP 不在/無効時は controller が no-op）。
//
// 冪等・非破壊: 同状態への遷移は再適用しない（高速トグル/連続 auto-off での flicker・二重 fetch を回避）。
//   reapply 未注入（MP 不在相当）は onLiveStateChange を no-op にする（例外を出さない）。

export class MpLiveModeCoordinator {
  // liveMode: FOLLOW 時の実効モード（既定 'ticklive'）。defaultMode: ANALYSIS で未選択時のフォールバック
  //   （catalog の MP mode 既定＝'normal'）。reapply: 実効モード再適用の副作用（関数・未注入で no-op）。
  constructor({ liveMode = 'ticklive', defaultMode = 'normal', reapply = null } = {}) {
    this._liveMode = liveMode;
    this._defaultMode = defaultMode;
    this._reapply = typeof reapply === 'function' ? reapply : null;
    this._isFollow = true;    // チャート既定 FOLLOW（初期はライブ追従）。
    this._mpUserMode = null;  // gear で選んだ分析モードの記憶。null=未選択（defaultMode へフォールバック）。
  }

  // 現在のチャート追従状態（true=FOLLOW / false=ANALYSIS）。
  isFollow() {
    return this._isFollow;
  }

  // 記憶している分析モード（gear 選択）。未選択なら null。
  userMode() {
    return this._mpUserMode;
  }

  // controller の MP param 構築（mpModeResolver）から呼ばれる。userMode（gear 選択）を記憶し、
  //   現在のライブ状態に応じた実効モードを返す。null は「記憶更新なし・実効解決のみ」（reapply 経路用）。
  resolve(userMode) {
    // gear 選択を「分析モード」として記憶（FOLLOW 中でも記憶だけは更新する）。ただし liveMode
    //   （'ticklive'）は分析モードになり得ない（仕様: 分析＝非 ticklive の選択モード）。legacy で
    //   mode:'ticklive' を保存済のインスタンスでも ANALYSIS が ticklive 継続しないよう記憶対象から除外し
    //   defaultMode へフォールバックさせる（review 🔵-2）。
    if (userMode != null && userMode !== this._liveMode) {
      this._mpUserMode = userMode;
    }
    return this._isFollow ? this._liveMode : this._analysisMode();
  }

  // ANALYSIS 時の実効モード（記憶モード優先・未選択は catalog 既定）。
  _analysisMode() {
    return this._mpUserMode != null ? this._mpUserMode : this._defaultMode;
  }

  // LiveFollowController の遷移フック。FOLLOW↔ANALYSIS の状態を更新し、実効モードを再適用させる。
  //   同状態への遷移は再適用しない（冪等＝連続 auto-off/高速トグルでの二重 fetch・flicker を回避）。
  onLiveStateChange(isFollow) {
    const next = !!isFollow;
    if (next === this._isFollow) {
      return; // 同状態は no-op。
    }
    this._isFollow = next;
    if (!this._reapply) {
      return; // MP 不在相当（reapply 未注入）。状態は更新済みで副作用なし。
    }
    // reapply は async（controller.reapplyMarketProfileMode）でありうる。fire-and-forget の拒否は
    //   握り潰して unhandledRejection 化を防ぐ（呼び出し元 _applyFollow/_applyAnalysis は同期）。
    const r = this._reapply();
    if (r && typeof r.then === 'function') {
      r.catch(() => {});
    }
  }
}
