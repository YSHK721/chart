// replay_market_profile_actor.js — ReplayMarketProfileActor（共有 MarketProfileActor の reveal 差分 subclass）。
//
// 設計入力: mp_full_modes_design.md「ReplayMarketProfileActor extends 共有 MarketProfileActor（fork 禁止・継承）」。
//   present full MarketProfileActor は normal/sessions/replay/ticklive の全モードを駆動する。単一ソース化
//   （symlink 共有）済みの基底を extends し、replay の reveal 差（因果 as-of / push 駆動 ticklive）だけを
//   override/追加する。基底の setParams/_applyMode/refresh/_fetchAt/setReplayCursor/_setReplay/_applySessions/
//   _applyProfileMargin/setEnabled/_ensureAttached/detach を再利用する（複製排除）。
//
//   reveal 差（override/追加）:
//     1. onLiveTick() override: isTicklive() なら no-op（push=enterBar/feedTick が育てる）／他は
//        super.refresh()（as-of-T＝getContext().to）。pull/push 二重駆動を遮断する。
//     2. _buildFormingArgs override: 基底の forming 引数に now(=getContext().to＝因果 T) と
//        from(=当日始まり=floor(now,86400)) を合流する（当日窓＝古典的 Market Profile）。
//     3. enterBar(now) 追加（slim 実装移設）: 先頭 self-guard `if(!isTicklive()) return;`。base=1・src=dwell・
//        now=T・from=当日始まり で forming 取得 → accumulator.init（tick 畳まず）→ base のみ描画・await ready。
//        バー入場ごとに accumulator を作り直す＝rollover 兼。
//     4. feedTick(sec,mid)/settleTick() 追加（slim 移設・throttle）: addTick は O(1)（HTTP 無）・snapshot は throttle。
//
//   override は subclass インスタンス限定（JS プロトタイプ継承）＝present 別インスタンスへ波及せず present
//   byte 挙動は不変。非破壊方針: forming 取得失敗（null）・base 必須フィールド欠損（空 profile）は前回描画を
//   保持する（NaN 混入を防ぐ＝既存 fetch null と同じ）。

import { MarketProfileActor } from './market_profile_actor.js';
import { GrowthWindow } from '../../domain/growth_window.js';
import { FORMING_MIN_INTERVAL_MS } from '../../replay/timing.js';

// MP-05 presence ガード（present actor と同基準）: base=1 応答の必須フィールド（レンジ/グリッド/base 配列）が
//   すべて有限/配列のときだけ true。欠損（無ローソク等の空 profile）は null 扱いで増分に入れず前回描画保持。
//   JSON の明示 null は Number(null)===0（有限）で誤通過するため、各必須数値は `!= null` を先に課す。
function _finiteNum(x) {
  return x != null && Number.isFinite(Number(x));
}
function _hasBaseFields(f) {
  return !!f
    && _finiteNum(f.priceMin)
    && _finiteNum(f.priceMax)
    && _finiteNum(f.nBins) && Number(f.nBins) > 0
    && _finiteNum(f.gridW) && Number(f.gridW) > 0
    && Array.isArray(f.baseFine);
}

export class ReplayMarketProfileActor extends MarketProfileActor {
  // 基底コンストラクタへ全 DI（client/primitive/mainSeries/getContext/replayBar/getCandles/renderer/
  //   formingClient/makeAccumulator）を委譲し、push 系 ticklive の throttle 時計だけ追加で保持する。
  //   now: ()->ms（throttle 用の時計・テストで注入）。throttleMs: snapshot 間隔（既定 FORMING_MIN_INTERVAL_MS）。
  constructor(opts = {}) {
    super(opts);
    const { now, throttleMs } = opts;
    this._now = typeof now === 'function'
      ? now
      : (typeof performance !== 'undefined' ? () => performance.now() : () => Date.now());
    this._throttleMs = Number.isFinite(throttleMs) ? throttleMs : FORMING_MIN_INTERVAL_MS;
    this._lastSnapMs = -1e9; // 最後に snapshot を描画した時刻（throttle 基準）。
    // 直近 forming グリッドの価格レンジ（isTickInGrid の範囲判定に使用）。未確定は null。
    this._gridPriceMin = null;
    this._gridPriceMax = null;
  }

  // override: push 成長中（normal/replay+growing＝isGrowingPush）は enterBar/feedTick が駆動するため
  //   onLiveTick（pull）を no-op で遮断する（pull/push 二重駆動の防止）。非 push（sessions+growing／非成長）は
  //   基底 refresh へ委譲し、getContext().to=T が as-seen-at-t として載る（因果・自動駆動）。sessions 成長は
  //   refresh(to,sessions) が育て（機構A）、非成長は as-of-T 再取得。
  //   Phase5: ゲートを isTicklive()（表示モード）から isGrowingPush()（成長軸）へ移行（ticklive セグメント撤去）。
  async onLiveTick() {
    if (this.isGrowingPush()) {
      return undefined; // push が育てる＝pull は駆動しない（二重駆動遮断）。
    }
    return super.refresh(); // 非 push: as-of-T（getContext().to）で再取得（因果）。
  }

  // override: 基底 forming 引数（{...ctx, ...params, src:'dwell', base, since}）へ now(=getContext().to＝
  //   因果 T) と from（base 累積の下限窓）を合流する。now は引数優先（enterBar が透過する T）、無指定時は
  //   getContext().to。
  //   Phase4（86400 隔離）: 旧実装は from=当日始まり(floor(now,86400)) を決め打ちしていた。これを
  //   GrowthWindow(mode,tf,cursor) へ委譲し隔離する。明示 from（呼び出し側指定）は優先（compat）。未指定時は
  //   GrowthWindow.from が窓を写像する: normal→絞った窓 min(当日始まり, formingStart)（視認性優先・ユーザー確定＝
  //   全期間累積だと 1 本ぶんの成長が極小で見えないため当日を base 下限に）、sessions→暦日 anchor。ただし sessions
  //   成長は Phase3 で refresh(to) へ倒れ本 forming 経路には到達しない。from=null（cursor 欠損）は載せない。
  //   全時間足の bar-period 成長は backend forming_ticks の period_start_unix(now,tf)（tf 依存 formingStart）が担う
  //   （reveal 窓 stream.js は不変）。
  _buildFormingArgs({ base, since, now, from } = {}) {
    const args = super._buildFormingArgs({ base, since });
    const effNow = now != null ? now : this._getContext().to;
    if (effNow != null) {
      args.now = effNow;
      if (from != null) {
        args.from = from; // 明示指定は温存（compat）。
      } else {
        const mode = this._sessions ? 'sessions' : 'normal';
        const w = GrowthWindow.forCurrent(mode, this._getContext().timeframe, effNow);
        if (w.from != null) {
          args.from = w.from; // normal=絞った窓 min(当日,formingStart)／sessions=暦日 anchor。cursor 欠損時のみ null。
        }
      }
    }
    return args;
  }

  // 追加: ticklive の push バー入場（render が now=T で呼ぶ）。バー入場＝rollover reset。
  //   fold 版（present 基底 _enterTicklive L266-302 の畳み込みに一致）: base=1・src=dwell・now=T・
  //   from=当日始まり で forming 取得 → accumulator を作り直し → forming.ticks を addTick で畳み込み →
  //   _lastSec 設定 → 描画。ただし縮退グリッド（forming tick 0 かつ priceMax-priceMin<=1）は描画スキップで
  //   前回描画を保持する（[0,1] 潰れを出さない＝最初の out-of-range tick で growTo が即グリッド確定）。
  async enterBar(now) {
    return this._rebuildAt(now, true); // skipDegenerateDraw=true（縮退は描かず前回保持）。
  }

  // 追加: 当日 tick がグリッド外へ出たとき（replay.js driver が発火）、now までの因果窓で forming を
  //   再取得しグリッドを拡張して作り直す。growTo は縮退スキップしない（拡張後は必ず描画＝当日プロファイル
  //   成長）。fold 実体は enterBar と共通（_rebuildAt）。now は「直近 revealed tick 秒」（未来リーク禁止）。
  async growTo(now) {
    return this._rebuildAt(now, false); // skipDegenerateDraw=false（拡張グリッドは描画）。
  }

  // enterBar/growTo の共通実体: base=1/src=dwell/now/from=GrowthWindow(normal→全期間・Phase4) で forming を
  //   [from, now] の因果窓で取得 → accumulator を作り直し（rollover/grid 拡張）→ forming.ticks を畳み込み
  //   （present 基底 _enterTicklive と同一 fold semantics）→ _lastSec/レンジ設定 → 描画。取得失敗（null）・
  //   base 欠損は前回描画を保持（非破壊）。skipDegenerateDraw=true かつ縮退グリッド（tick 0＋[..,+1] レンジ）は
  //   描画のみスキップ（init は行い growTo の土台を残す）。
  async _rebuildAt(now, skipDegenerateDraw) {
    if (!this.isGrowingPush()) {
      return; // 自己ガード: push 成長中（normal/replay+growing）以外は push 駆動しない（Phase5: 成長軸ゲート）。
    }
    if (!this._enabled || !this._formingClient || !this._makeAccumulator) {
      return;
    }
    const forming = await this._formingClient.fetchForming(
      this._buildFormingArgs({ base: 1, since: null, now }),
    );
    if (!forming) {
      return; // null は前回描画を保持（非破壊）。
    }
    if (!_hasBaseFields(forming)) {
      return; // 空 profile（必須フィールド欠損）は前回描画を保持（NaN 混入を防ぐ）。
    }
    const acc = this._makeAccumulator();
    acc.init({
      baseFine: forming.baseFine,
      baseKmin: forming.baseKmin,
      activeTable: forming.activeTable,
      priceMin: forming.priceMin,
      priceMax: forming.priceMax,
      nBins: forming.nBins,
      gridW: forming.gridW,
      formingStart: forming.formingStart,
    });
    this._accumulator = acc;
    this._formingStart = forming.formingStart;
    this._gridPriceMin = Number(forming.priceMin);
    this._gridPriceMax = Number(forming.priceMax);
    // forming.ticks を畳み込む（present 基底 _enterTicklive の fold と一致）。_lastSec は最終畳み込み秒。
    this._lastSec = null;
    const ticks = Array.isArray(forming.ticks) ? forming.ticks : [];
    for (const t of ticks) {
      acc.addTick(t[0], t[1]);
      this._lastSec = t[0];
    }
    this._lastSnapMs = this._now();
    // 縮退グリッド（forming tick 0 かつ priceMax-priceMin<=1）は描画スキップ＝[0,1] 潰れを出さない。
    if (skipDegenerateDraw && ticks.length === 0
        && (this._gridPriceMax - this._gridPriceMin) <= 1) {
      return; // 前回描画を保持（最初の out-of-range tick で growTo が即グリッド確定）。
    }
    this._draw();
  }

  // 追加: mid が直近 forming グリッドの価格レンジ内か（replay.js driver の growTo 発火判定に使用）。
  //   グリッド未確定（null）は false＝out-of-grid 扱いで growTo を促す。範囲は present-mode に忠実な
  //   forming の priceMin/priceMax（両端含む）。
  isTickInGrid(mid) {
    if (this._gridPriceMin == null || this._gridPriceMax == null) {
      return false;
    }
    const m = Number(mid);
    return m >= this._gridPriceMin && m <= this._gridPriceMax;
  }

  // 追加（slim 移設）: ライブ tick（animateForming が 1 点ずつ呼ぶ）。addTick は常に反映（O(1)・HTTP 無）、
  //   snapshot は throttle。停止/OFF/未 enter は no-op（addTick 停止＝停止で成長しない・MP OFF 無干渉）。
  //   de-dup: 畳み込み済み tick（sec<=_lastSec）は二重計上を防ぐため捨てる（present onLiveTick since と同型）。
  feedTick(sec, mid) {
    if (!this._enabled || !this._accumulator) {
      return;
    }
    if (this._lastSec != null && sec <= this._lastSec) {
      return; // 畳み込み済み範囲＝二重計上防止（de-dup ガード）。
    }
    this._accumulator.addTick(sec, mid);
    this._lastSec = sec;
    const t = this._now();
    if (t - this._lastSnapMs >= this._throttleMs) {
      this._lastSnapMs = t;
      this._draw();
    }
  }

  // 追加（slim 移設）: 確定時の最終 snapshot を強制描画する（throttle 無視）。
  settleTick() {
    if (!this._enabled || !this._accumulator) {
      return;
    }
    this._lastSnapMs = this._now();
    this._draw();
  }

  // 現在の accumulator の snapshot を primitive へ反映する（内部・push 系専用）。
  _draw() {
    if (this._primitive && typeof this._primitive.setProfile === 'function' && this._accumulator) {
      this._primitive.setProfile(this._accumulator.snapshot());
    }
  }
}
