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
  constructor({ liveUpdater, renderer, document, buttonId, mode }) {
    this._liveUpdater = liveUpdater ?? null;
    this._renderer = renderer ?? null;
    this._document = document ?? null;
    this._buttonId = buttonId;
    this._mode = MODE_FOLLOW; // 初期 FOLLOW。
    this._served = mode === 'b'; // B方式のみ活性。
    this._button = null;
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
  //   programmatic scrollToRealTime 由来の range イベント（atRightEdge=true）は FOLLOW 時 no-op で吸収する。
  _onRangeChange(atRightEdge) {
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
    if (scroll && this._renderer && typeof this._renderer.scrollToRealTime === 'function') {
      this._renderer.scrollToRealTime();
    }
    if (this._renderer && typeof this._renderer.setAnalysisTint === 'function') {
      this._renderer.setAnalysisTint(false);
    }
    this._setButtonActive(true);
  }

  // ANALYSIS を適用: LiveUpdater 停止＋背景 tint＋ボタン消灯（サーバ watch は継続）。
  _applyAnalysis() {
    this._mode = MODE_ANALYSIS;
    if (this._liveUpdater && typeof this._liveUpdater.stop === 'function') {
      this._liveUpdater.stop();
    }
    if (this._renderer && typeof this._renderer.setAnalysisTint === 'function') {
      this._renderer.setAnalysisTint(true);
    }
    this._setButtonActive(false);
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
