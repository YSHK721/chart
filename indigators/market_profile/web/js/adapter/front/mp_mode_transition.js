// mp_mode_transition.js — 表示モードの排他遷移ロール（ISSUE-181・SRP）。
//
// 設計入力（ISSUE-181）: MarketProfileActor は 6 アクター同居の神クラスで、その 1 つが
//   「表示モード遷移」（旧 market_profile_actor.js の setParams の mode 分岐 / legacy トグル受理 /
//   _applyMode / mpDisplayMode 台帳による遷移経路の選択）だった。変更要求の出所は
//   「normal / sessions / replay / ticklive の 4 モードをどう排他化し、どの解除一式を伴うか」のみで、
//   取得パラメータ写像・tick 逐次成長・リプレイ操作系・チャートレイアウト・日別タイル描画とは
//   独立している（各ロールの解除は host の既存メソッドへ委譲するだけ）。
//
// 状態も一緒に移す（ISSUE-181 対応方針・参照実装 mp_primitive_roles.js の分割手法に倣う）:
//   ticklive トグル（_ticklive）と sessions トグル（_sessions）を本クラスが所有する。
//   host はこれらを own field として持たず、prototype アクセサで旧読み取り面のみ維持する
//   （replay subclass replay_market_profile_actor.js:279,350,490 が `this._sessions` を読む）。
//
// host 契約（MpModeTransitionHost）が要求する最小メンバー（すべて呼び出し。代入しない）:
//   method: _exitTicklive（ticklive 解除＝成長エンジンの累積器破棄を含む）/ _setReplay /
//           _applySessions（sessions 一式解除）/ _applyMode（mode 明示指定時の遷移入口）/
//           _markSessionsFocusPending（日別初回フォーカスの pending 立て。実体は日別タイルロールが所有）
//   field : _growth（成長フラグの所有者 MpTickGrowth。ticklive 入場で setGrowing(true) を立てる）
//   メソッド呼び出しは host 経由でディスパッチする（subclass override を尊重＝抽出前の
//   `this.` 呼び出しと同一の仮想結合）。
//
// 挙動不変（ISSUE-181 の目的）: 各 case の呼び出し順序・_sessionsFocusPending を立てる条件
//   （**非 sessions → sessions の新規入場時のみ**）とその評価位置（_sessions=true の代入より前）は
//   抽出前と同一。ビュー（ズーム・スクロール・可視レンジ）への介入は本ロールでは一切行わない
//   （pending フラグを立てるだけで、focusTimeRange の発火は日別タイルロールが従来どおり行う。
//   ISSUE-164 裁定＝自動介入を増やしも減らしもしない）。

// 表示モード enum の単一台帳（transition 遷移経路・未知 mode の normal 吸収／ISSUE-134 OCP）。
import { mpDisplayMode } from '../../domain/mp_display_mode.js';

export class MpModeTransition {
  constructor(host) {
    this._host = host;
    this._ticklive = false; // ticklive モード ON/OFF（既定 OFF＝非増分・後方互換）。
    // sessions（日別プロファイル分割）ON/OFF。既定 false（通常の累積プロファイル・後方互換）。
    this._sessions = false;
  }

  // ---- 状態アクセス（host の prototype アクセサが委譲する読み取り面）----
  //   NOTE: host の isTicklive() / isSessions() は **_enabled ゲート付き**の公開述語であり意味が違う。
  //   取り違えを防ぐため、素のトグル値を返す本ロール側は ticklive() / sessions() と命名する。
  ticklive() { return this._ticklive; }

  sessions() { return this._sessions; }

  // ticklive トグルの解除（host の _exitTicklive が成長エンジン破棄と対で呼ぶ）。
  setTicklive(value) { this._ticklive = value; }

  // setParams の表示モード決定（mode 優先・legacy 後方互換）。取得パラメータ本体（bins/va/src/range）
  //   の取り込みは MpFetchParams が担い、本メソッドは「表示モードをどう動かすか」だけを決める。
  //   - mode 明示指定時のみ _applyMode を通し、legacy 分岐を評価しない（mode 優先＝二重管理を避ける）。
  //   - mode 未指定時のみ legacy トグル（旧 replay:true / sessions:true）を引き続き受理する。
  //     いずれも null/undefined は現状維持（未指定は潰さない）。
  //   呼び出し側（host.setParams）は本メソッドの後に必ず 1 回だけ _onParamsChanged を発火する
  //   （抽出前の 2 経路とも「最後に 1 回」で同一＝ISSUE-066 の伝播タイミング不変）。
  applyParams(params = {}) {
    if (params.mode != null) {
      this._host._applyMode(params.mode);
      return;
    }
    // legacy 受理（後方互換・mode 未指定時のみ）。旧 replay:true / sessions:true を引き続き受理する。
    //   sessions（日別プロファイル分割）トグル: true で refresh 時に context へ sessions:true を載せ、
    //   応答の profile.sessions を primitive/renderer へ反映。false で通常モードへ復帰。
    if (params.sessions != null) {
      this._sessions = !!params.sessions;
    }
    // replay トグル（増分1）。明示指定時のみ反映する（undefined は現状維持）。
    if (params.replay != null) {
      this._host._setReplay(!!params.replay);
    }
  }

  // 表示モードの排他遷移。既存の _setReplay / _applySessions 復元経路を再利用する（重複実装しない）。
  //   - 'sessions': replay 一式 OFF（_setReplay(false)＝バー非表示・T 縦線/トリム/スナップショット解除・
  //     カーソル null・チャート操作復元）＋ sessions ON（_sessions=true）。応答反映は後続 refresh で行う。
  //   - 'replay': sessions 一式 OFF（_sessions=false ＋ _applySessions(null)＝setSessions(null)・透明化解除）
  //     ＋ replay ON（_setReplay(true)）。
  //   - 'normal': 両 OFF 一式（_setReplay(false) ＋ _sessions=false ＋ _applySessions(null)）。
  //   排他が構造的に保証される（同時 ON が不可能）。未知の mode は 'normal' 扱い（安全側）。
  apply(mode) {
    const host = this._host;
    // 遷移経路は mp_display_mode 台帳（transition）が単一源。未知 mode は台帳が 'normal' へ吸収する
    //   （旧「未知の mode は 'normal' 扱い（安全側）」を台帳側へ集約＝新モードは台帳追記で完結・OCP）。
    switch (mpDisplayMode(mode).transition) {
      case 'ticklive': {
        // ticklive ON（tick 逐次成長）。replay/sessions 一式を解除して排他化する。
        host._setReplay(false);
        this._sessions = false;
        host._applySessions(null);
        this._ticklive = true;
        // ticklive モード＝成長 ON（Phase1 互換: mode が _growing を立てる）。成長フラグの実体は
        //   MpTickGrowth（A1）が所有するため、host の互換 setter ではなく所有者へ直接立てる
        //   （旧 `this._growing = true` と同一効果。host はフィールドを持たない）。
        host._growth.setGrowing(true);
        return;
      }
      case 'sessions': {
        host._exitTicklive();     // ticklive 解除（排他）。
        host._setReplay(false);   // replay 一式解除（バー/カーソル/トリム/スナップショット/操作）。
        // 自動ズームは **非 sessions → sessions の新規入場時のみ** pending にする。既に sessions のまま
        //   _applyMode('sessions') が再適用される（FOLLOW/ANALYSIS 遷移時の reapplyMarketProfileMode 等）
        //   ケースで pending を再セットすると、価格更新→自動 FOLLOW 復帰のたびに focus が再発火して
        //   ユーザーの手動ズームが「全体が初期表示」へリセットされる（実機バグ）。再適用では寄せない。
        if (!this._sessions) {
          host._markSessionsFocusPending();
        }
        this._sessions = true;    // sessions ON（応答の profile.sessions は refresh の _applySessions で反映）。
        return;
      }
      case 'replay': {
        host._exitTicklive();     // ticklive 解除（排他）。
        this._sessions = false;   // sessions OFF。
        host._applySessions(null); // sessions 一式解除（focus/ズーム/ロック・setSessions(null)・透明化解除）。
        host._setReplay(true);    // replay ON（バー表示）。
        return;
      }
      default: {
        // 'normal'（および未知値＝台帳が transition='normal' へ吸収）: 全 OFF 一式。
        host._exitTicklive();       // ticklive 解除（排他）。
        host._setReplay(false);
        this._sessions = false;
        host._applySessions(null);
      }
    }
  }
}
