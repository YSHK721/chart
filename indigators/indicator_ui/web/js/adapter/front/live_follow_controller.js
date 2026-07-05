// LiveFollowController（adapter/front/live_follow_controller.js）— チャート ライブ追従トグル。
//   present（B方式）固有。replay へは symlink しない（present だけがライブ追従を持つ）。
//
// 設計入力（確定仕様・状態機械）:
//   状態 _mode ∈ {FOLLOW, ANALYSIS}（初期 FOLLOW）。
//   - FOLLOW（既定）: LiveUpdater 稼働＋新足で右端追従（native shiftVisibleRangeOnNewBar）＋forming。
//       ボタン点灯。
//   - ANALYSIS（分析モード）: LiveUpdater.stop()＋右端追従なし＋背景 tint で状態明示。ボタン消灯。
//       サーバ watch は継続するため、再FOLLOW で catch-up（scrollToRealTime）できる。
//   - 自動遷移: 可視範囲購読で右端離脱→ANALYSIS／右端復帰→FOLLOW。手動ボタンでも切替。
//   - A方式（mode!=='b'）: ボタン disabled・配線しない（非活性）。
//
// 依存注入・隔離方針（DOM/lwc 非依存・全注入）:
//   - liveUpdater（start()/stop()）・renderer（subscribeVisibleRange/scrollToRealTime/setAnalysisTint）・
//     document（getElementById）・buttonId・mode を注入する。
//   - lwc の直叩きは一切しない（追従・tint・scroll はすべて renderer 経由＝upstream 隔離維持）。
//   - DOM/renderer 不在は no-op 防御（例外を出さない・SSR/テストで安全）。

const MODE_FOLLOW = 'FOLLOW';
const MODE_ANALYSIS = 'ANALYSIS';

export class LiveFollowController {
  constructor({ liveUpdater, renderer, document, buttonId, mode, onLiveStateChange } = {}) {
    this._liveUpdater = liveUpdater ?? null;
    this._renderer = renderer ?? null;
    this._document = document ?? null;
    this._buttonId = buttonId;
    // optional なライブ連動フック（present 固有）。FOLLOW/ANALYSIS 遷移で onLiveStateChange(isFollow) を呼ぶ。
    //   成長協調役（GrowthCoordinator）へ通知して MP 成長状態を growing↔static で連動させる（表示モードは維持）。
    //   未注入なら null＝一切呼ばない（既存ライブトグル挙動 byte 不変・MP 不在時も不変）。
    this._onLiveStateChange = typeof onLiveStateChange === 'function' ? onLiveStateChange : null;
    this._mode = MODE_FOLLOW; // 初期 FOLLOW。
    this._served = mode === 'b'; // B方式のみ活性。
    this._button = null;
    // programmatic scroll（scrollToRealTime）由来の可視範囲イベント抑制フラグ。
    //   実 lwc の scrollToRealTime は非同期/アニメで、右端収束前に stale(panned) な範囲を報告する。
    //   その stale(atRightEdge=false) で FOLLOW→ANALYSIS へ auto-off されると手動 FOLLOW が即取消されるため、
    //   scroll 発火中は auto-off を抑止し、右端収束(atRightEdge=true)を観測した時点で解除する。
    this._suppressAutoOff = false;
    // 直近の右端在否（range イベントで更新）。初期は右端（fitContent 初期表示は通常右端＝true）と仮定する。
    //   _applyFollow は「既に右端なら scrollToRealTime は no-op でイベントを発火せず arm が解除されず stuck する」
    //   ため、この値を見て『実際に移動する時（右端に居ない時）だけ』arm + scroll する（code-review 🔴 の是正）。
    this._lastAtRightEdge = true;
  }

  // 現在モード（'FOLLOW' | 'ANALYSIS'）の読み取り専用アクセサ。
  get mode() {
    return this._mode;
  }

  // 配線＋初期 FOLLOW 適用。A方式（mode!=='b'）はボタンを disabled にして配線しない。
  install() {
    this._button = (this._document && typeof this._document.getElementById === 'function')
      ? this._document.getElementById(this._buttonId)
      : null;

    // A方式: ボタンを非活性化し、追従ロジックを一切配線しない（LiveUpdater も起動しない）。
    if (!this._served) {
      if (this._button) {
        this._button.disabled = true;
      }
      return this;
    }

    // ボタン click → 手動トグル（ボタン不在は配線をスキップ＝no-op 防御）。
    if (this._button && typeof this._button.addEventListener === 'function') {
      this._button.disabled = false;
      this._button.addEventListener('click', () => this.toggleManual());
    }

    // 可視範囲購読 → 自動遷移（renderer 不在・API 未提供は配線しない＝no-op 防御）。
    if (this._renderer && typeof this._renderer.subscribeVisibleRange === 'function') {
      this._renderer.subscribeVisibleRange((atRightEdge) => this._onRangeChange(atRightEdge));
    }

    // 初期 FOLLOW の「表示」を適用する（点灯＋tint 解除）。ただし LiveUpdater の初回 start は
    //   ここでは行わない: 初回起動はデータ ready 後に入口（index.html）が既存経路で担う（データ未取得
    //   時点でライブ更新を走らせない＝既存挙動を byte 不変で保つ）。以降のモード遷移（ANALYSIS↔FOLLOW）は
    //   本 controller が stop()/start() を所有する（start は冪等）。初期は catch-up scroll もしない。
    this._mode = MODE_FOLLOW;
    if (this._renderer && typeof this._renderer.setAnalysisTint === 'function') {
      this._renderer.setAnalysisTint(false);
    }
    this._setButtonActive(true);
    return this;
  }

  // 手動トグル: FOLLOW↔ANALYSIS。FOLLOW→ANALYSIS は tint on・stop、ANALYSIS→FOLLOW は
  //   start・scrollToRealTime（catch-up）・tint off。
  toggleManual() {
    if (this._mode === MODE_FOLLOW) {
      this._applyAnalysis();
    } else {
      this._applyFollow(true);
    }
  }

  // 可視範囲変化ハンドラ（振動防止の核）:
  //   FOLLOW && !atRightEdge → ANALYSIS ／ ANALYSIS && atRightEdge → FOLLOW ／ 同状態 no-op。
  _onRangeChange(atRightEdge) {
    // 直近の右端在否を常に記録する（_applyFollow の scroll 要否判定に使う）。stale イベントも含め更新するが、
    //   scroll 収束後の最終イベントは true になるため、収束後は正しく true が残る。
    this._lastAtRightEdge = atRightEdge;
    // programmatic scroll（scrollToRealTime）由来の stale イベント抑制:
    //   武装中（_suppressAutoOff）は FOLLOW→ANALYSIS の auto-off を抑止する。scroll が右端へ収束し
    //   atRightEdge=true を観測した時点で武装解除する（以降の genuine パン離脱では通常どおり auto-off）。
    //   （arch の「programmatic scroll 由来イベントは atRightEdge=true で no-op 吸収＝suppression 不要」前提は
    //     実機の非同期 scroll で崩れる＝収束前に panned 範囲を報告するため、明示的 suppression を置く。）
    if (this._suppressAutoOff) {
      if (atRightEdge) {
        this._suppressAutoOff = false; // scroll が右端へ収束。以降は通常判定へ戻す。
      }
      return; // 収束前の stale(false) では auto-off しない（不具合の核）。
    }
    if (this._mode === MODE_FOLLOW && !atRightEdge) {
      this._applyAnalysis();
    } else if (this._mode === MODE_ANALYSIS && atRightEdge) {
      this._applyFollow(true);
    }
    // 同状態は no-op（何もしない）。
  }

  // FOLLOW を適用: LiveUpdater 起動＋（再FOLLOW時のみ）最新足へ catch-up＋tint 解除＋ボタン点灯。
  _applyFollow(scroll) {
    this._mode = MODE_FOLLOW;
    if (this._liveUpdater && typeof this._liveUpdater.start === 'function') {
      this._liveUpdater.start(); // 冪等（多重 start 無害）。
    }
    // 実際に移動する時（＝まだ右端に居ない時）だけ arm + scrollToRealTime する。既に右端(_lastAtRightEdge=true)
    //   なら scroll は no-op で range イベントを発火せず、arm が解除イベントを得られず stuck するため呼ばない
    //   （既に右端なので catch-up scroll 自体も不要）＝code-review 🔴 の是正。
    if (scroll && !this._lastAtRightEdge
        && this._renderer && typeof this._renderer.scrollToRealTime === 'function') {
      // scroll 発火の前に武装する（実 lwc は非同期・fake は同期発火 — どちらも収束前 stale を抑止するため先に立てる）。
      this._suppressAutoOff = true;
      this._renderer.scrollToRealTime();
    }
    if (this._renderer && typeof this._renderer.setAnalysisTint === 'function') {
      this._renderer.setAnalysisTint(false);
    }
    this._setButtonActive(true);
    // ライブ連動: FOLLOW 遷移を協調役へ通知（MP を ticklive へ）。未注入は no-op（byte 不変）。
    this._notifyLiveState(true);
  }

  // ANALYSIS を適用: LiveUpdater 停止＋背景 tint＋ボタン消灯（サーバ watch は継続）。
  _applyAnalysis() {
    this._mode = MODE_ANALYSIS;
    // ANALYSIS 化で programmatic scroll の抑制窓を閉じる（ANALYSIS 中に true が来たら auto-on を正しく通す）。
    this._suppressAutoOff = false;
    if (this._liveUpdater && typeof this._liveUpdater.stop === 'function') {
      this._liveUpdater.stop();
    }
    if (this._renderer && typeof this._renderer.setAnalysisTint === 'function') {
      this._renderer.setAnalysisTint(true);
    }
    this._setButtonActive(false);
    // ライブ連動: ANALYSIS 遷移を協調役へ通知（MP を選択モードへ）。未注入は no-op（byte 不変）。
    this._notifyLiveState(false);
  }

  // ライブ連動フックの呼び出し（未注入は no-op）。フック内例外が本 controller の状態機械へ波及しないよう
  //   防御捕捉する（連動失敗でライブトグル本体が壊れない＝回帰ゼロ）。
  _notifyLiveState(isFollow) {
    if (!this._onLiveStateChange) {
      return;
    }
    try {
      this._onLiveStateChange(isFollow);
    } catch {
      // 連動側の失敗はライブトグルの状態遷移に影響させない（防御）。
    }
  }

  // ボタン点灯/消灯（既存トグル書式 is-active + aria-pressed に合わせる）。ボタン不在は no-op。
  _setButtonActive(on) {
    if (!this._button) {
      return;
    }
    if (this._button.classList && typeof this._button.classList.toggle === 'function') {
      this._button.classList.toggle('is-active', on);
    }
    if (typeof this._button.setAttribute === 'function') {
      this._button.setAttribute('aria-pressed', String(on));
    }
  }
}
