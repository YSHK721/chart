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

// forming（足内成長）非対応 tf（backend forming_bar.is_supported_timeframe と一致＝1W/1M は固定 floor 不可で
//   非対応）。この tf は enterBar/growTo の forming 取得が 400→null（非破壊）になり push 成長で描けないため、
//   refresh override は基底 refresh（全期間 as-of）へ委譲して従来描画を保つ（1W/1M の描画欠落を防ぐ）。
const _FORMING_UNSUPPORTED_TF = new Set(['1W', '1M']);

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
    // ISSUE-047: 成長 push 中の binw ロック（barw）。key=`from|tf|bins` が変わるまで再計算しない
    //   （＝同一成長セッション内で固定・再生開始点/時間足/bins 変更で自動再導出）。null=ロック不能。
    this._barwLockKey = null;
    this._barwLock = null;
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

  // override: reveal 成長中（growing push）の refresh を因果 base（enterBar）へ振り替える。
  //   基底 refresh は /market_profile を to=getContext().to（as-of-T）で引き、その T までの revealed 足を集計した
  //   「その時間足の完成プロファイル（base 窓より広い as-of-T 完成形・未来リークではない）」を描く。これが
  //   reveal（因果成長）では、成長開始前に一瞬この完成形を描いてから enterBar が base 窓（現在形成足）へ作り直す
  //   ＝完成足フラッシュ→リセット→成長、という因果的に不自然な開始シーケンスを生む（実測: setEnabled(true)→基底
  //   refresh が as-of-T 完成 setProfile を発火）。growing push では基底 refresh の完成形を描かず、現在カーソル
  //   now=getContext().to の因果 base 窓（forming・空 forming＝再生点の開始形）で開始する。
  //   cursor 未確定（restore/初期化＝setupReplay 前で untilTime 未設定）は「何も描かない」: index.html は
  //   controller.restore() を setupReplay()（untilTime を設定する唯一の場所）より前に実行するため、可視 MP の
  //   復元がここへ cursor=undefined で到達する。基底 refresh へ委譲すると全期間（完成形）を描いて
  //   フラッシュが再発する（実測・再発報告）ため、最初の描画は再生 1 フレーム目 enterBar の因果 base に遅延する。
  //   非 growing push（sessions/static）は基底 refresh（as-of-T）へ委譲＝回帰なし。
  //   本 override は replay subclass インスタンス限定（present 基底 actor は無改変）。
  async refresh() {
    const ctx = this._getContext();
    const cursor = ctx.to;
    if (this.isGrowingPush() && !_FORMING_UNSUPPORTED_TF.has(ctx.timeframe)) {
      if (cursor == null) {
        return undefined; // cursor 未確定＝未来リーク禁止で描かない（再生 1 フレーム目の enterBar が初描画）。
      }
      return this.enterBar(cursor); // 因果 base 窓（driver 未配線 from はフォールバック）。
    }
    // 非 growing push（sessions/static）・forming 非対応 tf（1W/1M）は基底 refresh へ委譲。
    //   1W/1M は forming で描けない（enterBar→null）ため従来の全期間 as-of 描画を保つ（描画欠落を防ぐ）。
    return super.refresh();
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
    // ISSUE-047（再生中のバースケール変動）: 成長 push の bins モードは、enterBar/growTo のたびに backend が
    //   binw=(累積窓レンジ/bins) を再導出するため、レンジ拡大のたびにプロファイル全体（バー高さ barH・
    //   norm 正規化）が再スケールする。成長中は from 直前の因果履歴レンジから barw を 1 回だけ導出して固定し、
    //   resmode=range（barw 固定・bin 数可変）で送る＝スケール安定（依頼者承認・案 a）。ユーザー明示の
    //   resmode=range は温存（上書きしない）。ロック不能（履歴なし等）は従来 bins のまま（非破壊）。
    if (this.isGrowingPush() && args.resmode !== 'range' && args.from != null) {
      const barw = this._growthBarwLock(args.from);
      if (barw != null) {
        args.resmode = 'range';
        args.range = barw;
      }
    }
    return args;
  }

  // ISSUE-047 の barw ロックを返す（内部）。key=`from|tf|bins` 単位でメモ化し、同一成長セッション内は
  //   固定値を返す（再生開始点 from・時間足・bins のいずれかが変わったときだけ GrowthWindow.lockedBarw で
  //   再導出＝stale ロックを引き回さない）。導出は domain 純関数へ委譲（このメソッドはメモ化のみ）。
  //   ロック不能（null）はメモ化しない（レビュー🟡）: 初回時点で getCandles が履歴未ロードでも、揃った
  //   次回呼び出しで再試行して復帰する（成功値のみキャッシュ・失敗の恒久固定でセッション全体が bins
  //   フォールバックに沈黙残存するのを防ぐ。再試行コストは candles 走査 O(n) で毎バー許容）。
  _growthBarwLock(from) {
    const tf = this._getContext().timeframe;
    const bins = this._params ? this._params.bins : undefined;
    const key = `${from}|${tf}|${bins ?? ''}`;
    if (this._barwLockKey === key && this._barwLock != null) {
      return this._barwLock;
    }
    const barw = GrowthWindow.lockedBarw(this._getCandles(), from, tf, bins);
    if (barw != null) {
      this._barwLockKey = key;
      this._barwLock = barw;
    }
    return barw;
  }

  // 追加: ticklive の push バー入場（render が now=T で呼ぶ）。バー入場＝rollover reset。
  //   fold 版（present 基底 _enterTicklive L266-302 の畳み込みに一致）: base=1・src=dwell・now=T・
  //   from で forming 取得 → accumulator を作り直し → forming.ticks を addTick で畳み込み →
  //   _lastSec 設定 → 描画。ただし縮退グリッド（forming tick 0 かつ priceMax-priceMin<=1）は描画スキップで
  //   前回描画を保持する（[0,1] 潰れを出さない＝最初の out-of-range tick で growTo が即グリッド確定）。
  //   from（任意）: base 累積の下限窓（UNIX 秒）。replay.js driver が「再生開始点 replayStart の time」を渡す
  //   （＝再生範囲から累積・日跨ぎでも非リセット）。省略時は _buildFormingArgs が GrowthWindow フォールバック
  //   （当日窓）を写像する（driver 未配線の controller seam / gear 経路）。
  async enterBar(now, from) {
    return this._rebuildAt(now, true, from); // skipDegenerateDraw=true（縮退は描かず前回保持）。
  }

  // 追加: 当日 tick がグリッド外へ出たとき（replay.js driver が発火）、now までの因果窓で forming を
  //   再取得しグリッドを拡張して作り直す。growTo は縮退スキップしない（拡張後は必ず描画＝当日プロファイル
  //   成長）。fold 実体は enterBar と共通（_rebuildAt）。now は「直近 revealed tick 秒」（未来リーク禁止）。
  //   from（任意）: enterBar と同じ replayStart 累積下限（driver が透過）。
  async growTo(now, from) {
    return this._rebuildAt(now, false, from); // skipDegenerateDraw=false（拡張グリッドは描画）。
  }

  // enterBar/growTo の共通実体: base=1/src=dwell/now/from で forming を [from, now] の因果窓で取得 →
  //   accumulator を作り直し（rollover/grid 拡張）→ forming.ticks を畳み込み（present 基底 _enterTicklive と
  //   同一 fold semantics）→ _lastSec/レンジ設定 → 描画。取得失敗（null）・base 欠損は前回描画を保持（非破壊）。
  //   skipDegenerateDraw=true かつ縮退グリッド（tick 0＋[..,+1] レンジ）は描画のみスキップ（init は行い growTo
  //   の土台を残す）。
  //   from（任意・driver=replayStart）: 明示指定時は不変条件 from<=formingStart を保つため
  //   min(from, periodStart(now,tf)) へクランプしてから _buildFormingArgs へ渡す（replayStart>formingStart の端＝
  //   1W/1M ラベル規約や当該バー内でも未来リークしない）。省略時は _buildFormingArgs の GrowthWindow フォール
  //   バックへ委譲する。
  async _rebuildAt(now, skipDegenerateDraw, from) {
    if (!this.isGrowingPush()) {
      return; // 自己ガード: push 成長中（normal/replay+growing）以外は push 駆動しない（Phase5: 成長軸ゲート）。
    }
    if (!this._enabled || !this._formingClient || !this._makeAccumulator) {
      return;
    }
    let effFrom = from;
    if (effFrom != null && Number.isFinite(Number(now))) {
      // クランプ: from<=formingStart（不変条件）・from<=now（未来リーク禁止）。formingStart<=now ゆえ十分。
      effFrom = Math.min(Number(effFrom), GrowthWindow.periodStart(Number(now), this._getContext().timeframe));
    }
    const forming = await this._formingClient.fetchForming(
      this._buildFormingArgs({ base: 1, since: null, now, from: effFrom }),
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
