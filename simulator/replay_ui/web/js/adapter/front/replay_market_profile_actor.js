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
  }

  // override: ticklive は push（enterBar/feedTick）が駆動するため onLiveTick（pull）を no-op で遮断する
  //   （pull/push 二重駆動の防止）。非ticklive（normal/sessions/replay）は基底 refresh へ委譲し、
  //   getContext().to=T が as-seen-at-t として載る（因果・自動駆動）。
  async onLiveTick() {
    if (this.isTicklive()) {
      return undefined; // push が育てる＝pull は駆動しない（二重駆動遮断）。
    }
    return super.refresh(); // 非ticklive: as-of-T（getContext().to）で再取得（因果）。
  }

  // override: 基底 forming 引数（{...ctx, ...params, src:'dwell', base, since}）へ now(=getContext().to＝
  //   因果 T) と from(=当日始まり=floor(now,86400)) を合流する。combined=[当日始まり, now) の古典的
  //   Market Profile（当日 TPO 形成）。now は引数優先（enterBar が透過する T）、無指定時は getContext().to。
  _buildFormingArgs({ base, since, now, from } = {}) {
    const args = super._buildFormingArgs({ base, since });
    const effNow = now != null ? now : this._getContext().to;
    if (effNow != null) {
      args.now = effNow;
      args.from = from != null ? from : Math.floor(effNow / 86400) * 86400;
    }
    return args;
  }

  // 追加（slim 移設）: ticklive の push バー入場（render が now=T で呼ぶ）。先頭 self-guard で非ticklive は
  //   no-op（normal/sessions/replay は refresh 経路が駆動＝既存 render フック不変で誤駆動しない）。
  //   base=1・src=dwell・now=T・from=当日始まり で forming 取得 → accumulator を作り直し（rollover reset）→
  //   base のみ描画。forming tick は畳まない（feedTick が育てる＝二重計上回避）。await で ready 保証。
  //   無効時・取得失敗（null）・base 欠損時は前回描画を保持（非破壊）。
  async enterBar(now) {
    if (!this.isTicklive()) {
      return; // 自己ガード: ticklive 以外では push 駆動しない（既存フック不変）。
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
    this._lastSec = null;
    this._lastSnapMs = this._now();
    this._draw(); // base のみ描画。
  }

  // 追加（slim 移設）: ライブ tick（animateForming が 1 点ずつ呼ぶ）。addTick は常に反映（O(1)・HTTP 無）、
  //   snapshot は throttle。停止/OFF/未 enter は no-op（addTick 停止＝停止で成長しない・MP OFF 無干渉）。
  feedTick(sec, mid) {
    if (!this._enabled || !this._accumulator) {
      return;
    }
    this._accumulator.addTick(sec, mid);
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
