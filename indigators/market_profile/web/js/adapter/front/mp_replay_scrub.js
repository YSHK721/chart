// mp_replay_scrub.js — リプレイ（当時プロファイル）・T スクラブのロール（ISSUE-181・SRP）。
//
// 設計入力（ISSUE-181）: MarketProfileActor は 6 アクター同居の神クラスで、その 1 つが
//   「リプレイ・スクラブ」（旧 market_profile_actor.js の _replayExtra / _applySnapshot /
//   _setReplay / onReplayControlsChange / isReplay / setReplayCursor）だった。変更要求の出所は
//   「リプレイバー（anchor|rolling・スナップショット）と T カーソルの操作系」のみで、
//   表示モード遷移・tick 逐次成長・日別タイル・チャートレイアウトとは独立している。
//
// 状態も一緒に移す（ISSUE-181 対応方針・参照実装 mp_primitive_roles.js の分割手法に倣う）:
//   replay ON/OFF（_replay）・当時カーソル T（_replayTo）・スクラブ coalesce の in-flight／
//   末尾要求（_scrubRunning / _scrubQueued）・リプレイバー参照（_bar）を本クラスが所有する。
//   host（MarketProfileActor）はこれらのフィールドを持たない。
//
// host 契約（MpReplayScrubHost）が要求する最小メンバー（すべて read／呼び出し。代入しない）:
//   field : _enabled（MP 有効フラグ）/ _primitive（setCursorTime/setSnapshot）/ _renderer
//           （setCandleTrim/setUserInteraction）/ _getCandles / _getContext
//   method: setReplayCursor（末尾実行の再入。subclass override を尊重するため host 経由）
//           _applySnapshot（同上）/ _fetchAt（to=T の 1 回取得。replay subclass が再利用する接合面）
//
// 挙動不変（ISSUE-181 の目的）: 判定順序・ガード・呼び出し順（カーソル即時反映 → snapshot 反映 →
//   coalesce → fetch → 末尾実行）は抽出前と同一。ビュー（ズーム・スクロール）への介入は
//   本ロールでは一切行わない（ISSUE-164 裁定・新規追加も削除もしない）。

// timeframe → 足の秒長（rolling 窓の from 算出）。actor と同一の単一情報源を参照する。
import { TF_BAR_SEC } from '../../domain/tf_meta.js';

// 増分2 定数（試作 prototype_260630-01 と一致）。
const ROLL_BARS = 60; // ローリング窓の本数（from = T - ROLL_BARS*bar_sec）。

export class MpReplayScrubController {
  constructor(host, replayBar) {
    this._host = host;
    // リプレイスライダバー（任意注入・setVisible/setCandles/mode/isSnapshot/currentTime）。未注入時 null。
    this._bar = replayBar ?? null;
    // リプレイ（増分1）状態。replay=ON でバー表示・T スクラブで to 付き再取得（coalesce）。
    this._replay = false;
    this._replayTo = null;      // 現在の T（UNIX 秒）。null=最新（全期間）。
    this._scrubRunning = false; // in-flight フラグ（scrubProfile coalesce・移植元 prototype_260630-01）。
    this._scrubQueued = null;   // in-flight 中に来た最後の T（末尾実行用）。
  }

  isReplay() {
    return this._replay;
  }

  // 現在の当時カーソル T（未設定は null）。
  cursor() {
    return this._replayTo;
  }

  // 増分2: リプレイ取得の追加コンテキスト（from/today）を現在のモード/スナップショット状態から組む。
  //   - ローリングモード: from = T - ROLL_BARS*bar_sec（T 直前 60 本の窓）。アンカーは from を載せない。
  //   - スナップショット ON: today=true（当日強調用の today[]/today_max を要求）。OFF は載せない。
  //   移植元 prototype_260630-01 params()（asofmode/asoftrim）。replayBar 未注入時は空（後方互換）。
  replayExtra(time) {
    const extra = {};
    if (!this._bar) {
      return extra;
    }
    // 注意: ここでの mode は「リプレイバーの anchor モード」（anchor|rolling）であり、MP 表示モード
    //   enum（normal/sessions/replay/ticklive・mp_display_mode 台帳）とは別 enum である（ISSUE-134）。
    //   'rolling' は表示モードの値ではないため mp_display_mode 台帳には含めず、ここは replayBar 契約の
    //   anchor モード判定として維持する。
    const anchorMode = typeof this._bar.mode === 'function' ? this._bar.mode() : 'anchor';
    if (anchorMode === 'rolling' && time != null) {
      const tf = this._host._getContext().timeframe;
      const barSec = TF_BAR_SEC[tf] ?? 86400;
      extra.from = time - ROLL_BARS * barSec;
    }
    if (typeof this._bar.isSnapshot === 'function' && this._bar.isSnapshot()) {
      extra.today = true;
    }
    return extra;
  }

  // 増分2: スナップショット状態を反映する（ローソクトリム＋primitive の減光/today 描画）。
  //   snapshot ON: ローソクを T までトリム（renderer.setCandleTrim(T)）・primitive.setSnapshot(true)。
  //   snapshot OFF: トリム解除（setCandleTrim(null)）・primitive.setSnapshot(false)。
  //   renderer/primitive の該当メソッド非提供時は skip（後方互換）。
  applySnapshot(time) {
    const host = this._host;
    const on = !!(this._bar && typeof this._bar.isSnapshot === 'function'
      && this._bar.isSnapshot());
    if (host._renderer && typeof host._renderer.setCandleTrim === 'function') {
      host._renderer.setCandleTrim(on && time != null ? time : null);
    }
    if (host._primitive && typeof host._primitive.setSnapshot === 'function') {
      host._primitive.setSnapshot(on);
    }
  }

  // replay ON/OFF を反映する。ON: バー表示（candles は composition root が別途 setCandles 済み）。
  //   OFF: バー非表示・T 縦線消去・T をリセット（全期間へ復帰）。移植元 prototype_260630-01。
  setReplay(on) {
    const host = this._host;
    this._replay = on;
    if (this._bar) {
      // ON 時に最新 candles をバーへ供給（min/max・index→time の元）。timeframe 切替後も現在足に追従。
      if (on && typeof this._bar.setCandles === 'function') {
        this._bar.setCandles(host._getCandles());
      }
      // 初期カーソルを現在のスライダ位置（既定=右端=最新）に設定して T 縦線を即描画する。
      //   スライダは右端から始まるため、スクラブ前でも線が出る（ユーザFB「スナップショットONで
      //   T 縦線が出ない」の修正）。fetch はしない（線＝setCursorTime のみ）。onControlsChange の
      //   初期化元にもなり、スクラブ前にスナップショットを ON にしても当時 T が確定する。
      if (on && this._replayTo == null && typeof this._bar.currentTime === 'function') {
        const t0 = this._bar.currentTime();
        if (t0 != null) {
          this._replayTo = t0;
          if (host._primitive && typeof host._primitive.setCursorTime === 'function') {
            host._primitive.setCursorTime(t0);
          }
        }
      }
      if (typeof this._bar.setVisible === 'function') {
        this._bar.setVisible(on);
      }
    }
    if (!on) {
      // OFF: 当時カーソルを解除し、T 縦線を消す（primitive.setCursorTime(null)）。
      this._replayTo = null;
      this._scrubQueued = null;
      if (host._primitive && typeof host._primitive.setCursorTime === 'function') {
        host._primitive.setCursorTime(null);
      }
      // 増分2: スナップショットのローソクトリムを解除し（全ローソク復元）、primitive の減光を消す。
      if (host._renderer && typeof host._renderer.setCandleTrim === 'function') {
        host._renderer.setCandleTrim(null);
      }
      if (host._primitive && typeof host._primitive.setSnapshot === 'function') {
        host._primitive.setSnapshot(false);
      }
      // 防御: スワイプ捕捉中（setUserInteraction(false)）のまま gear で OFF にされても
      // チャート操作を必ず復元する（冪等・未捕捉時も無害）。
      if (host._renderer && typeof host._renderer.setUserInteraction === 'function') {
        host._renderer.setUserInteraction(true);
      }
    }
  }

  // 増分2: リプレイバーのモード（アンカー/ローリング）・スナップショット変更を受け、現在 T で再取得する。
  //   replayBar.onChange から配線する。無効時（replay OFF / disabled / T 未設定）は no-op。
  async onControlsChange() {
    const host = this._host;
    if (!host._enabled || !this._replay) {
      return;
    }
    // カーソル未設定（スクラブ前）でも、現在のスライダ位置（既定=最新）を当時 T として初期化する。
    //   これによりスクラブせずスナップショットを ON にしても当時プロファイル・T 縦線が反映される。
    let t = this._replayTo;
    if (t == null && this._bar && typeof this._bar.currentTime === 'function') {
      t = this._bar.currentTime();
    }
    if (t == null) {
      return;
    }
    await host.setReplayCursor(t);
  }

  // リプレイ T スクラブ: T（対応足の time・UNIX 秒）を当時カーソルに設定し、to=T で当時プロファイルを
  //   再取得して primitive へ反映する。連続スクラブは coalesce（in-flight 中は最後の T だけ末尾実行＝
  //   移植元 prototype_260630-01 scrubProfile）。無効時（replay OFF / disabled）は no-op。
  async setCursor(time) {
    const host = this._host;
    if (!host._enabled || !this._replay) {
      return;
    }
    this._replayTo = time;
    // T 縦線は即時反映（fetch 完了を待たずカーソルを動かす＝プロト applyAsofView 相当）。
    if (host._primitive && typeof host._primitive.setCursorTime === 'function') {
      host._primitive.setCursorTime(time);
    }
    // 増分2: スナップショットのローソクトリム/減光を即時反映（fetch を待たず＝プロト applyAsofView）。
    host._applySnapshot(time);
    // coalesce: in-flight 中は最後の要求だけを queue し、完了後に末尾実行する。
    if (this._scrubRunning) {
      this._scrubQueued = time;
      return;
    }
    this._scrubRunning = true;
    try {
      await host._fetchAt(time);
    } finally {
      this._scrubRunning = false;
    }
    if (this._scrubQueued != null) {
      const last = this._scrubQueued;
      this._scrubQueued = null;
      await host.setReplayCursor(last); // 末尾実行（最後の T のみ）。
    }
  }
}
