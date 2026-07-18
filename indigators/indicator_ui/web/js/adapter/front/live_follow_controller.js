// LiveFollowController（adapter/front/live_follow_controller.js）— チャート ライブ追従トグル。
//   present（B方式）固有。replay へは symlink しない（present だけがライブ追従を持つ）。
//
// 設計入力（確定仕様・状態機械）:
//   状態 _mode ∈ {FOLLOW, ANALYSIS}（初期 FOLLOW）。
//   - FOLLOW（既定）: LiveUpdater 稼働＋新足で右端追従（native shiftVisibleRangeOnNewBar）＋forming。
//       ボタン点灯。
//   - ANALYSIS（分析モード）: LiveUpdater.stop()＋右端追従なし＋背景 tint で状態明示。ボタン消灯。
//       サーバ watch は継続するため、再FOLLOW で catch-up（scrollToRealTime）できる。
//   - 切替は**ライブボタンのクリックのみ**（ISSUE-118 ユーザー裁定 2026-07-18）。旧仕様の自動遷移
//     （可視範囲購読の右端離脱→ANALYSIS／右端復帰→FOLLOW・EPS/抑制機構つき）は削除した。
//     過去へスクロールしてもモード・背景は変わらず、再FOLLOW クリックで catch-up scroll する。
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
  constructor({ liveUpdater, liveTickPlayer, formingBarUpdater, renderer, document, buttonId, mode, onLiveStateChange } = {}) {
    this._liveUpdater = liveUpdater ?? null;
    // ライブ価格の書き手（ISSUE-049 の LiveTickPlayer・FormingBarUpdater）も FOLLOW/ANALYSIS で
    //   start/stop する。これらを止めないと ANALYSIS でも価格が更新され続け（＝「ライブ」トグルが効かない）、
    //   さらに更新→自動 FOLLOW 復帰→sessions 再 focus で手動ズームがリセットされる（実機バグの根治）。
    this._liveActors = [liveUpdater, liveTickPlayer, formingBarUpdater].filter(Boolean);
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
    // 旧・自動遷移用の状態（_suppressAutoOff / _lastAtRightEdge）は ISSUE-118 で自動遷移ごと削除。
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

    // 自動遷移（可視範囲購読の右端離脱/復帰）は ISSUE-118 で削除＝切替は手動クリックのみ。

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

  // FOLLOW を適用: LiveUpdater 起動＋（再FOLLOW時）最新足へ catch-up＋tint 解除＋ボタン点灯。
  //   ISSUE-118: 自動遷移が無くなったため catch-up は無条件（右端に既に居る場合の scroll は無害な no-op。
  //   旧実装の抑制機構 _suppressAutoOff/_lastAtRightEdge は auto-off が存在しないため不要）。
  _applyFollow(scroll) {
    this._mode = MODE_FOLLOW;
    // ライブ更新系（LiveUpdater＋LiveTickPlayer＋FormingBarUpdater）を全て起動（冪等・多重 start 無害）。
    for (const a of this._liveActors) {
      if (a && typeof a.start === 'function') a.start();
    }
    if (scroll && this._renderer && typeof this._renderer.scrollToRealTime === 'function') {
      this._renderer.scrollToRealTime();
    }
    if (this._renderer && typeof this._renderer.setAnalysisTint === 'function') {
      this._renderer.setAnalysisTint(false);
    }
    this._setButtonActive(true);
    // ライブ連動: FOLLOW 遷移を協調役へ通知（MP を成長 ON＝growing=true へ／Phase5: 旧 ticklive モード置換は
    //   撤廃し成長軸で駆動）。未注入は no-op（byte 不変）。
    this._notifyLiveState(true);
  }

  // ANALYSIS を適用: LiveUpdater 停止＋背景 tint＋ボタン消灯（サーバ watch は継続）。
  _applyAnalysis() {
    this._mode = MODE_ANALYSIS;
    // ライブ更新系を全て停止（ANALYSIS＝ライブ更新停止＝価格を凍結）。これで ANALYSIS 中に価格が更新されず、
    //   手動ズームがリセットされない（Bug A/B の根治）。
    for (const a of this._liveActors) {
      if (a && typeof a.stop === 'function') a.stop();
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
