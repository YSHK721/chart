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
import { mpSourceCapability } from '../../domain/mp_source_capability.js';
import { sessionDayStart } from '../../domain/session_day.js';
import { FORMING_MIN_INTERVAL_MS } from '../../replay/timing.js';

// forming（足内成長）非対応 tf（backend forming_bar.is_supported_timeframe と一致＝1W/1M は固定 floor 不可で
//   非対応）。この tf は enterBar/growTo の forming 取得が 400→null（非破壊）になり push 成長で描けないため、
//   refresh override は基底 refresh（全期間 as-of）へ委譲して従来描画を保つ（1W/1M の描画欠落を防ぐ）。
// ISSUE-134（OCP・言語跨ぎミラー）: 権威は Python 単一台帳 marketdata.resample.TF_DESCRIPTORS の
//   floorable=false から導出される marketdata.tf_meta.NON_FLOORABLE_TF（＝{'1W','1M'}）。JS 側は HTTP
//   バンドルに Python を持ち込めないため、この定数を py↔js 対で維持する既存慣行（session_day 等と同様）に
//   従い明示ミラーとして複製する。カレンダー足を追加する際は Python 台帳と本 Set を対で更新すること。
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

// ISSUE-133（SRP）: _rebuildAt/feedTick に二重分岐で内包していた「増分 push 成長（dwell 系）」と
//   「非増分 as-of coalesce（zp 系）」の 2 戦略を Strategy として抽出し注入化した。actor はソース能力
//   （mpSourceCapability(src).incremental）で戦略を選び委譲する（分岐は選択のみに縮退）。各戦略は actor を
//   context（``a``）に受け、actor 内部状態（_accumulator/_lastSec/_rebuildSeq/_gridDegenerate/_clockSec 等）を
//   抽出前と同一手順で操作する＝挙動不変。純オブジェクト（副作用は actor 経由のみ・テストで差替可能）。

// 増分 push 戦略（dwell 系）: forming を [from, now] の因果窓で base=1 取得 → accumulator を作り直し
//   （rollover/grid 拡張）→ forming.ticks を畳み込み（present 基底 _enterTicklive と同一 fold）→ 描画。
//   feedTick は addTick（O(1)・HTTP 無）＋ throttle snapshot。縮退グリッドは描画のみ抑止（前回描画保持）。
const INCREMENTAL_PUSH_STRATEGY = {
  async rebuildAt(a, now, from, syncClamp) {
    if (!a._enabled || !a._formingClient || !a._makeAccumulator) {
      return;
    }
    // ISSUE-050 同期クランプ（blank 禁止・enterBar のみ）: await fetchForming（実測 ≈180ms）の解決前に、旧
    //   accumulator のスナップショットを新カーソルの revealed 上限へ同期クランプして即描画する（stale
    //   プロファイル・フラッシュを断つ）。
    if (syncClamp) {
      a._clampDrawToRevealed();
    }
    let effFrom = from;
    if (effFrom != null && Number.isFinite(Number(now))) {
      // クランプ: from<=formingStart（不変条件）・from<=now（未来リーク禁止）。formingStart<=now ゆえ十分。
      effFrom = Math.min(Number(effFrom), GrowthWindow.periodStart(Number(now), a._getContext().timeframe));
    }
    // 世代ガード（profSeq 相当）: この fetch の世代を採番し、await の戻りが最新でなければ setProfile を破棄する。
    const seq = ++a._rebuildSeq;
    const forming = await a._formingClient.fetchForming(
      a._buildFormingArgs({ base: 1, since: null, now, from: effFrom }),
    );
    if (seq !== a._rebuildSeq) {
      return; // 古い応答は破棄（後続 _rebuildAt が発行済み＝最新のみ反映・stale 上書き防止）。
    }
    if (!forming) {
      return; // null は前回描画を保持（非破壊）。
    }
    if (!_hasBaseFields(forming)) {
      return; // 空 profile（必須フィールド欠損）は前回描画を保持（NaN 混入を防ぐ）。
    }
    const acc = a._makeAccumulator();
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
    a._accumulator = acc;
    a._formingStart = forming.formingStart;
    a._gridPriceMin = Number(forming.priceMin);
    a._gridPriceMax = Number(forming.priceMax);
    // forming.ticks を畳み込む（present 基底 _enterTicklive の fold と一致）。_lastSec は最終畳み込み秒。
    a._lastSec = null;
    const ticks = Array.isArray(forming.ticks) ? forming.ticks : [];
    for (const t of ticks) {
      acc.addTick(t[0], t[1]);
      a._lastSec = t[0];
    }
    a._lastSnapMs = a._now();
    // ISSUE-049: 縮退グリッド（forming tick 0 かつ priceMax-priceMin<=1）を状態化する。縮退中は描画抑止
    //   （前回描画保持）。実グリッド確定（非縮退の rebuild）で解除＝描画再開する。init 自体は行う。
    a._gridDegenerate = ticks.length === 0
      && (a._gridPriceMax - a._gridPriceMin) <= 1;
    if (a._gridDegenerate) {
      return; // 前回描画を保持（最初の out-of-range tick で growTo が即グリッド確定）。
    }
    a._draw();
  },

  // ライブ tick（animateForming が 1 点ずつ供給）。addTick は常に反映（O(1)・HTTP 無）、snapshot は throttle。
  //   de-dup: 畳み込み済み tick（sec<=_lastSec）は二重計上を防ぐため捨てる（present onLiveTick since と同型）。
  feedTick(a, sec, mid) {
    if (!a._accumulator) {
      return;
    }
    if (a._lastSec != null && sec <= a._lastSec) {
      return; // 畳み込み済み範囲＝二重計上防止（de-dup ガード）。
    }
    a._accumulator.addTick(sec, mid);
    a._lastSec = sec;
    // ISSUE-049: 縮退グリッド中は throttle 描画も抑止（前回描画保持）。addTick 自体は行う（範囲外 clip・O(1)）。
    if (a._gridDegenerate) {
      return;
    }
    const t = a._now();
    if (t - a._lastSnapMs >= a._throttleMs) {
      a._lastSnapMs = t;
      a._draw();
    }
  },
};

// 非増分 as-of coalesce 戦略（zp 系）: accumulator を持たず、単一時計（_clockSec＝直近リビール秒）を
//   前進させてから coalesce 付き as-of 再計算（super.refresh・_scheduleNonIncrementalRefresh）を発火する。
//   実行は fire-and-forget＋最新 1 回 coalesce（再生非干渉・前回描画保持）。描画は完了時の setProfile が担う。
const AS_OF_COALESCE_STRATEGY = {
  rebuildAt(a, now, from, syncClamp) {
    // ISSUE-129: 単一時計をカーソル秒（enterBar/growTo の now）で更新してから発火する。
    // ISSUE-130: 1D はバー入場（syncClamp=true）の因果フロンティアがラベル（UTC 深夜）でなく
    //   **セッション始端**。growTo（tick 前進）は実リビール秒のまま。
    if (Number.isFinite(Number(now))) {
      const n = Number(now);
      a._clockSec = (syncClamp && a._getContext().timeframe === '1D')
        ? sessionDayStart(n)
        : n;
    }
    a._scheduleNonIncrementalRefresh();
    return undefined;
  },

  // 足内リビール秒で単一時計を前進（単調・順不同 tick を防御）させ、coalesce 付き as-of 再計算を発火する。
  feedTick(a, sec, _mid) {
    const s = Number(sec);
    if (Number.isFinite(s) && (a._clockSec == null || s > a._clockSec)) {
      a._clockSec = s;
    }
    a._scheduleNonIncrementalRefresh();
  },
};

export class ReplayMarketProfileActor extends MarketProfileActor {
  // 基底コンストラクタへ全 DI（client/primitive/mainSeries/getContext/replayBar/getCandles/renderer/
  //   formingClient/makeAccumulator）を委譲し、push 系 ticklive の throttle 時計だけ追加で保持する。
  //   now: ()->ms（throttle 用の時計・テストで注入）。throttleMs: snapshot 間隔（既定 FORMING_MIN_INTERVAL_MS）。
  constructor(opts = {}) {
    super(opts);
    const { now, throttleMs } = opts;
    // ISSUE-133（SRP）: 増分 push（dwell）/ 非増分 as-of coalesce（zp）の 2 戦略を注入化（既定は module 定数）。
    //   src はリクエストごとに変わりうる（setParams）ため、_rebuildAt/feedTick が能力ゲートで戦略を選ぶ。
    this._incrementalStrategy = opts.incrementalStrategy || INCREMENTAL_PUSH_STRATEGY;
    this._asOfStrategy = opts.asOfStrategy || AS_OF_COALESCE_STRATEGY;
    this._now = typeof now === 'function'
      ? now
      : (typeof performance !== 'undefined' ? () => performance.now() : () => Date.now());
    this._throttleMs = Number.isFinite(throttleMs) ? throttleMs : FORMING_MIN_INTERVAL_MS;
    this._lastSnapMs = -1e9; // 最後に snapshot を描画した時刻（throttle 基準）。
    // 直近 forming グリッドの価格レンジ（isTickInGrid の範囲判定に使用）。未確定は null。
    this._gridPriceMin = null;
    this._gridPriceMax = null;
    // ISSUE-050 世代ガード（参照 prototype_260630-01 fetchProfileOnly の profSeq 相当）: _rebuildAt が
    //   await fetchForming を跨ぐたびに単調増加させ、戻りが最新 seq でなければ setProfile を破棄する
    //   （後方/連続スクラブで古い応答が新描画を上書きしない）。
    this._rebuildSeq = 0;
    // ISSUE-047: 成長 push 中の binw ロック（barw）。key=`from|tf|bins` が変わるまで再計算しない
    //   （＝同一成長セッション内で固定・再生開始点/時間足/bins 変更で自動再導出）。null=ロック不能。
    this._barwLockKey = null;
    this._barwLock = null;
    // ISSUE-049: 現在の accumulator グリッドが縮退（[0,1]・base 空/tick 0）か。縮退中は feedTick/
    //   settleTick/growTo の描画も抑止する（前回描画保持＝バー全消滅フラッシュを出さない）。
    this._gridDegenerate = false;
    // ISSUE-129（単一時計）: 非増分 src（zp）の現在時刻＝直近リビール秒（to をバー粒度から秒粒度へ
    //   細粒度化する）。enterBar/growTo の now と feedTick の sec で前進し、後退スクラブは
    //   enterBar(now=旧バー time) が巻き戻す（stale 未来時計を引き回さない）。null=未確定（上書きしない）。
    this._clockSec = null;
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
    // ISSUE-138（日別 sessions の足内育成×逆スクラブ）: sessions は accumulator/forming を使わず as-of
    //   refresh（機構A）で育てるため、本メソッド（reveal seam onLiveRecompute が毎バー呼ぶ）が as-of refresh の
    //   バー入場点になる。ここで単一時計 _clockSec をリビールフロンティア（getContext().to＝現在バー T）へ
    //   巻き戻し、後退スクラブで stale な未来時計（feedTick が足内で前進させた値）を引き回さない
    //   （＝zp の enterBar 巻き戻し規約と同義。直接代入＝後退も反映・単調 max ではない）。cursor 未確定
    //   （to=null・restore 前）は上書きしない。非 sessions は _clockSec を触らず従来不変（回帰ゼロ）。
    if (this._sessions) {
      const cursor = this._getContext().to;
      if (cursor != null) {
        this._clockSec = Number(cursor);
      }
    }
    return super.refresh(); // 非 push: as-of-T（getContext().to／sessions は _clockSec）で再取得（因果）。
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
    if (this.isGrowingPush() && !_FORMING_UNSUPPORTED_TF.has(ctx.timeframe)
        && mpSourceCapability(this._params.src).incremental) {
      if (cursor == null) {
        return undefined; // cursor 未確定＝未来リーク禁止で描かない（再生 1 フレーム目の enterBar が初描画）。
      }
      return this.enterBar(cursor); // 因果 base 窓（driver 未配線 from はフォールバック）。
    }
    // 非 growing push（sessions/static）・forming 非対応 tf（1W/1M）・非増分 src（zp・ISSUE-120）は
    //   基底 refresh へ委譲。1W/1M は forming で描けない（enterBar→null）ため従来の全期間 as-of 描画を保つ。
    //   非増分 src は forming（dwell 原子・当日窓）を駆動すると選択 src と異なる原子の当日窓に差し替わる
    //   （present の _isIncremental と同じ能力ゲート＝ゲート規則の対称化）。
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
    // syncClamp=true: バー変更（カーソル移動）＝ await fetchForming の解決前に旧プロファイルを新カーソルの
    //   revealed 上限へ同期クランプして stale プロファイル・フラッシュを断つ（ISSUE-050）。縮退グリッドの
    //   描画抑止は _rebuildAt/feedTick/settleTick 内の _gridDegenerate 状態で担う（ISSUE-049）。
    return this._rebuildAt(now, from, true);
  }

  // 追加: 当日 tick がグリッド外へ出たとき（replay.js driver が発火）、now までの因果窓で forming を
  //   再取得しグリッドを拡張して作り直す。fold 実体は enterBar と共通（_rebuildAt）。拡張後の実グリッドは
  //   描画され、縮退のまま（データ無バー等）は前回描画を保持する（ISSUE-049・enterBar と同一基準）。
  //   now は「直近 revealed tick 秒」（未来リーク禁止）。
  //   from（任意）: enterBar と同じ replayStart 累積下限（driver が透過）。
  async growTo(now, from) {
    // syncClamp=false: グリッド拡張（同一カーソル）は同期クランプしない（成長中の revealed 超 tick を
    //   誤って clip しないため）。世代ガードは _rebuildAt 内で常時適用される。縮退グリッドの描画抑止は
    //   _rebuildAt/feedTick/settleTick 内の _gridDegenerate 状態で担う（ISSUE-049）。
    return this._rebuildAt(now, from, false);
  }

  // ISSUE-124: 非増分 src の as-of refresh を直列 coalesce で走らせる（多重発火は最新 1 回に畳む）。
  //   super.refresh() は実行時点の getContext().to を読む＝pending 消化時に最新カーソルで再計算される。
  //   例外は握って busy を確実に解放する（以降のバーで再スケジュールされる＝自己回復）。
  _scheduleNonIncrementalRefresh() {
    if (this._nonIncRefreshBusy) {
      this._nonIncRefreshPending = true;
      return;
    }
    this._nonIncRefreshBusy = true;
    const run = async () => {
      try {
        do {
          this._nonIncRefreshPending = false;
          await super.refresh();
        } while (this._nonIncRefreshPending);
      } catch {
        // 取得失敗は前回描画保持（非破壊）。busy 解放は finally が担う。
      } finally {
        this._nonIncRefreshBusy = false;
      }
    };
    run();
  }

  // enterBar/growTo の共通実体: base=1/src=dwell/now/from で forming を [from, now] の因果窓で取得 →
  //   accumulator を作り直し（rollover/grid 拡張）→ forming.ticks を畳み込み（present 基底 _enterTicklive と
  //   同一 fold semantics）→ _lastSec/レンジ設定 → 描画。取得失敗（null）・base 欠損は前回描画を保持（非破壊）。
  //   縮退グリッド（tick 0＋[..,+1] レンジ）は描画のみスキップし _gridDegenerate を立てる（init は行い
  //   growTo の土台を残す・ISSUE-049）。
  //   from（任意・driver=replayStart）: 明示指定時は不変条件 from<=formingStart を保つため
  //   min(from, periodStart(now,tf)) へクランプしてから _buildFormingArgs へ渡す（replayStart>formingStart の端＝
  //   1W/1M ラベル規約や当該バー内でも未来リークしない）。省略時は _buildFormingArgs の GrowthWindow フォール
  //   バックへ委譲する。
  async _rebuildAt(now, from, syncClamp = false) {
    if (!this.isGrowingPush()) {
      return; // 自己ガード: push 成長中（normal/replay+growing）以外は push 駆動しない（Phase5: 成長軸ゲート）。
    }
    // ISSUE-133（SRP）: 能力ゲートで戦略を選び委譲する（抽出前の二重分岐と同一手順）。
    //   非増分 src（zp）は as-of coalesce 戦略（ISSUE-120/124/129/130）、増分 src（dwell）は push 戦略。
    if (!mpSourceCapability(this._params.src).incremental) {
      return this._asOfStrategy.rebuildAt(this, now, from, syncClamp);
    }
    return this._incrementalStrategy.rebuildAt(this, now, from, syncClamp);
  }

  // override（ISSUE-129・単一時計）: 非増分 src（zp）×成長 push 中は、fetch の to をリビール秒
  //   （_clockSec）へ細粒度化する（ctx の後に spread され to を上書き）。to はリプレイの現在時刻
  //   そのもの（as-seen-at-t の T）であり、backend は now=to で境界日をライブ同一の経過分クランプ
  //   ＝1D でも日内推移が成長する。candle 切断（time<=to）はバー粒度でも秒粒度でも同一集合＝挙動不変。
  //   静止（非成長）は ctx.to（＝untilTime＝T）がそのまま時計なので上書き不要。増分 src・時計未確定は
  //   空＝従来 URL 不変（present は基底の no-op seam のまま非波及）。
  //   ISSUE-127: 契約は UNIX 秒（整数）。1分OHLC 等の合成 tick は小数秒を生むため必ず floor する。
  _clockExtra() {
    if (this._usesAsOfClock() && this._clockSec != null) {
      return { to: Math.floor(this._clockSec) };
    }
    return {};
  }

  // ISSUE-138（as-of 時計の適用条件・SRP）: 非増分 as-of coalesce 戦略（単一時計 _clockSec を feedTick で
  //   足内前進＋coalesce 再計算・onLiveTick でバー入場巻き戻し）で駆動するか。
  //   - 日別 sessions: accumulator/forming を使わず as-of refresh で当日を育てる（機構A）ため src 能力に
  //     依らず対象＝ライブ同一の「リビール tick 毎に当日プロファイル再計算」を成立させる。
  //   - 非 sessions: 従来どおり「成長 push 中×非増分 src（zp）」のみ。!_sessions のとき本式は
  //     (isGrowingPush() && !incremental) へ厳密一致する＝非 sessions（normal/replay/ticklive）は 1 バイト不変。
  _usesAsOfClock() {
    return !!this._sessions
      || (this.isGrowingPush() && !mpSourceCapability(this._params.src).incremental);
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
    if (!this._enabled) {
      return;
    }
    // ISSUE-133（SRP）: 能力ゲートで戦略を選び委譲する（抽出前の二重分岐と同一手順・同一非対称性）。
    //   非増分 as-of coalesce 戦略（accumulator を持たず単一時計を前進＋coalesce 再計算）の対象は
    //   _usesAsOfClock()＝「日別 sessions（機構A）または 成長 push 中×非増分 src（zp）」（ISSUE-138）。
    //   それ以外（増分 src／非成長・非 sessions）は push 戦略（addTick＋throttle snapshot・従来経路不変）。
    if (this._usesAsOfClock()) {
      this._asOfStrategy.feedTick(this, sec, mid);
      return;
    }
    this._incrementalStrategy.feedTick(this, sec, mid);
  }

  // 追加（slim 移設）: 確定時の最終 snapshot を強制描画する（throttle 無視）。
  //   縮退グリッド中は描かない（ISSUE-049・前回描画保持＝feedTick と同一基準）。
  settleTick() {
    if (!this._enabled || !this._accumulator || this._gridDegenerate) {
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

  // ISSUE-050: revealed 上限（現在カーソルまでの revealed ローソクの high 最大）を返す。_getContext().to まで
  //   slice 済みの _getCandles()（replay の renderer.getCandles＝enterBar 前に fold 済み）末尾までの high 最大＝
  //   ローソク上端。getCandles 未注入/空/high 非有限は null（＝クランプ不能＝present 非波及・既存挙動不変）。
  _revealedPriceMax() {
    const candles = this._getCandles();
    if (!Array.isArray(candles) || candles.length === 0) {
      return null;
    }
    let max = -Infinity;
    for (const c of candles) {
      const h = c == null ? NaN : Number(c.high);
      if (Number.isFinite(h) && h > max) {
        max = h;
      }
    }
    return Number.isFinite(max) ? max : null;
  }

  // ISSUE-050: 旧 accumulator のスナップショットを revealed 上限へ同期クランプして即描画する。
  //   revealed 上限が不明（getCandles 未注入/空）または accumulator 未確立なら no-op（前回描画保持＝非破壊・
  //   present 非波及）。それ以外は clamp 結果を必ず即描画する:
  //     - 部分重複（revealed 域内 bin が残る・例 1455→1429）: revealed 超 bin を落とした clamped を描く。
  //     - 完全非重複（旧プロファイルが全て revealed 超・例 1499→400）: clamp は空 bin になり、空プロファイルを
  //       描いて旧 stale 広プロファイルを消す（blank）。これは ISSUE-049（成長中の縮退＝**revealed 域に有効 bin
  //       が有る**のに全消滅させる禁止）とは保護対象が異なり非矛盾＝revealed 域に有効 bin が無いのが正直な状態。
  //       keep-stale だと 1499 帯の MP が 400 帯のローソク上へ ≈180ms 浮き、ISSUE-050 同クラスの欠陥を再生する
  //       ため blank が正しい（architecture-executor 観点5）。いずれも await fetchForming 解決で as-of-T へ自己修正。
  _clampDrawToRevealed() {
    if (!this._accumulator || !this._primitive
        || typeof this._primitive.setProfile !== 'function') {
      return;
    }
    const revealedMax = this._revealedPriceMax();
    if (revealedMax == null) {
      return; // revealed 上限不明＝クランプ不能（present 非波及・既存挙動不変）。
    }
    const clamped = _clampProfileToMax(this._accumulator.snapshot(), revealedMax);
    if (clamped) {
      this._primitive.setProfile(clamped); // 部分重複=clamped 描画／完全非重複=空描画で stale 消去（blank）。
    }
  }
}

// ISSUE-050: revealed 上限 max を超える価格帯 bin/POC/VA を落としたプロファイルを返す（純・snapshot 非破壊・
//   primitive 無改変）。bins は price<=max のみ残す（primitive はゼロ幅 bin でも MIN_BAR_PX を描くため、tpo=0 化
//   でなく除去で上端を抑える）。POC/VAH/VAL は revealed 超なら null（水平線を引かない＝上端超えを出さない）。
//   price_max も min(price_max,max) へクランプ。実データ範囲を超えない古典 MP と同型の同期描画にする。
function _clampProfileToMax(profile, max) {
  if (!profile || !Number.isFinite(Number(max))) {
    return profile;
  }
  const m = Number(max);
  const dropHi = (p) => (p != null && Number(p) > m ? null : p);
  const out = {
    ...profile,
    bins: profile.bins,
    poc: dropHi(profile.poc),
    va_high: dropHi(profile.va_high),
    va_low: dropHi(profile.va_low),
    price_max: profile.price_max != null ? Math.min(Number(profile.price_max), m) : profile.price_max,
  };
  if (Array.isArray(profile.bins)) {
    const kept = profile.bins.filter((b) => b != null && Number(b.price) <= m);
    // bin 除去に整合させて派生量を再計算する（clamped DTO の自己整合＝architecture-executor 低重大度の是正）。
    //   norm は**残存 bin の最大 tpo 基準**へ引き直す（除去した高値 bin が POC でもバー幅が正しい）。snapshot
    //   と同型（tmax=0 は 1 フォールバック）。tpo_units/n_bins も残存に一致させる。
    let tmax = 0;
    let sum = 0;
    for (const b of kept) {
      const t = Number(b.tpo) || 0;
      sum += t;
      if (t > tmax) {
        tmax = t;
      }
    }
    const denom = tmax > 0 ? tmax : 1;
    out.bins = kept.map((b) => ({ ...b, norm: Math.round(((Number(b.tpo) || 0) / denom) * 10000) / 10000 }));
    out.n_bins = kept.length;
    out.tpo_units = Math.round(sum);
  }
  return out;
}
