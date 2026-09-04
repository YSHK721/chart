// GrowthCoordinator（usecase/growth_coordinator.js）— 表示モードと成長状態を直交化する共有協調役。
//   Model A 統一成長モデル: 表示モード（normal/replay/sessions）× 成長状態（growing/static）を直交化する。
//   present（B方式）が LiveFollowController の FOLLOW/ANALYSIS 遷移を単一の成長信号へ写像する。
//   replay も後半で同一協調役を用いる布石として market_profile の共有 usecase へ移設（旧 indicator_ui adapter）。
//
// 役割再フレーム（旧 MpLiveModeCoordinator からの変更）:
//   旧: FOLLOW→MP 実効モードを 'ticklive' へ置換／ANALYSIS→gear 記憶モード。
//   新: 表示モードは常に「ユーザーが gear で選んだ記憶モード（未選択は defaultMode='normal'）」を維持する
//       （resolve は 'ticklive' を返さない）。ライブ/分析の差は成長状態のみ＝FOLLOW→growing=true /
//       ANALYSIS→growing=false（isGrowing）。これを actor.applyGrowthState({growing}) の単一信号へ写像する。
//
// 配線（composition_root_front で注入）:
//   - resolve(userMode): IndicatorController の MP param 構築（mpModeResolver）から呼ばれる。userMode を記憶し、
//       現在の選択表示モードを返す（成長状態には依存しない・副作用は記憶のみ）。null は「記憶更新なし・解決のみ」。
//   - isGrowing(): 現在の成長状態（FOLLOW=true / ANALYSIS=false）。controller が growing 信号として読む。
//   - onLiveStateChange(isFollow): LiveFollowController の遷移フック。状態を更新し reapply で実効を再適用させる。
//   - reapply: 現在表示中 MP へ mode 維持＋growing トグルを再適用する副作用（controller 経由・不在時 no-op）。
//
// 冪等・非破壊: 同状態への遷移は再適用しない（高速トグル/連続 auto-off の flicker・二重 fetch を回避）。
//   reapply 未注入（MP 不在相当）は onLiveStateChange を no-op にする（例外を出さない）。

export class GrowthCoordinator {
  // defaultMode: gear 未選択時のフォールバック表示モード（catalog の MP mode 既定＝'normal'）。
  //   reapply: mode 維持＋growing トグル再適用の副作用（関数・未注入で no-op）。
  constructor({ defaultMode = 'normal', reapply = null } = {}) {
    this._defaultMode = defaultMode;
    this._reapply = typeof reapply === 'function' ? reapply : null;
    this._isFollow = true;    // チャート既定 FOLLOW（初期はライブ追従＝成長 ON）。
    this._mpUserMode = null;  // gear で選んだ表示モードの記憶。null=未選択（defaultMode へフォールバック）。
  }

  // 現在のチャート追従状態（true=FOLLOW / false=ANALYSIS）。
  isFollow() {
    return this._isFollow;
  }

  // 現在の成長状態（FOLLOW=growing / ANALYSIS=static）。actor.applyGrowthState の growing へ写像する。
  isGrowing() {
    return this._isFollow;
  }

  // 記憶している表示モード（gear 選択）。未選択なら null。
  userMode() {
    return this._mpUserMode;
  }

  // controller の MP param 構築（mpModeResolver）から呼ばれる。userMode（gear 選択）を記憶し、
  //   現在の選択表示モードを返す（成長状態に依存しない）。null は「記憶更新なし・解決のみ」（reapply 経路用）。
  //   'ticklive' は返さない（成長はモードでなく growing 信号が担う＝直交化）。
  resolve(userMode) {
    if (userMode != null) {
      this._mpUserMode = userMode;
    }
    return this._mpUserMode != null ? this._mpUserMode : this._defaultMode;
  }

  // LiveFollowController の遷移フック。FOLLOW↔ANALYSIS の状態を更新し、実効（mode 維持＋growing）を再適用させる。
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
