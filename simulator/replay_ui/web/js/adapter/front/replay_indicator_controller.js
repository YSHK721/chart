// replay_indicator_controller.js — reveal（因果リビール）対応の IndicatorController 派生。
//
// 設計入力: frontend_unification_design.md「indicator_controller (B) 双方向差分」段階移行 item5。
//   共有 present IndicatorController（../front/indicator_controller.js＝symlink 単一ソース）を extends し、
//   replay 固有の reveal 差だけを override/追加する（fork＝全文複製は禁止）。共有ベースは reveal 用の
//   inert seam（_extraComputeFields / forceTail / _untilTime gate 付き enterBar-union / onLiveTick typeof gate）
//   を持ち、present では byte 挙動不変（present テストで固定）。本 subclass はその seam を実装で埋める。
//
// reveal 差（present との違い）:
//   - untilTime（そのフレームの時点 T）: setUntilTime で設定し compute へ素通し（_extraComputeFields）。
//   - forming（足内更新の形成中バー）: recomputeFormingLatest が INTRABAR_FORMING_IDS の末尾点のみ
//     forceTail 差分再計算し compute へ素通しする。
//   - MP（Market Profile）: ReplayMarketProfileActor（共有 MarketProfileActor の subclass）駆動。/compute を
//     持たないため recomputeInstance は _recomputeMarketProfile へ委譲し、mode-aware で ticklive→enterBar /
//     normal・sessions・replay→refresh(as-of-T) に振り分ける。ticklive の成長自体は setupReplay
//     （render→enterBar / animateForming→feedTick）が actor を直接駆動する。_mpParams は基底の rich 実装
//     （mode/resmode/range/src/bins/va 全 param）を再利用し、全 4 モードを機能させる。

import { IndicatorController } from './indicator_controller.js';
import { CAUSAL_REVEAL_IDS } from '../../usecase/causal_reveal_ids.js';
import { INTRABAR_FORMING_IDS } from '../../usecase/intrabar_forming_ids.js';

// [reveal 一括] ソート済み time 配列で t 以下の点数を返す（二分探索・revealTo のスライス位置）。
function upperBound(ts, t) {
  let lo = 0;
  let hi = Array.isArray(ts) ? ts.length : 0;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (ts[mid] <= t) lo = mid + 1; else hi = mid;
  }
  return lo;
}

// [reveal] 足内追従の対象指標リストは共有モジュール（usecase/intrabar_forming_ids.js・symlink 経由
//   ＝ライブと同一実体）へ移管した（2026-07-22 統一設計・リスト内容は不変）。末尾差分の実体も
//   基底 IndicatorController.recomputeFormingTails（forceTail 差分）に単一化し、本 subclass は
//   forming seam（形成中バーの素通し）だけを担う。

export class ReplayIndicatorController extends IndicatorController {
  constructor(opts) {
    super(opts);
    // [reveal] untilTime（再生のその時点・UNIX秒）。undefined=ライブ（present）。setUntilTime で設定し
    //   compute へ素通しする（_extraComputeFields）。present は本フィールドを持たないため seam は inert。
    this._untilTime = undefined;
    // [reveal] forming（足内更新中の形成中バー暫定 OHLC）。undefined=確定足のまま計算。
    this._forming = undefined;
    this._winStart = undefined;   // [ISSUE-238] 足内窓（形成中バーの実 tick 数算出用）
    this._winEnd = undefined;
    // [reveal 一括・ISSUE-158 ②] 事前一括計算の基底キャッシュ。instanceId →
    //   { def, params, series（F3 検証済み全レンジ payload）, times（系列名→ソート済 time 配列）}。
    //   対象は CAUSAL_REVEAL_IDS（実測で per-step と乖離 0 を確認済みの因果指標）のみ。
    this._revealCache = new Map();
    // 無効化世代（clearRevealCache/invalidate 後に届いた遅延応答を破棄する）。
    this._revealEpoch = 0;
  }

  // ================= [reveal 一括・ISSUE-158 ②] =================
  //   再生開始時に全レンジを 1 回計算し（buildRevealBase）、以降のバー送りは revealTo(t) の
  //   同期スライス描画のみ（バーごとの HTTP を発行しない）。値の同一性は登録リストの実測
  //   ゲートで担保（causal_reveal_ids.js 参照）。未登録指標・MP は従来経路のまま。

  // リビール対象（適用済み ∩ 登録リスト ∩ 非 MP）。
  _revealTargets() {
    return [...this._state.applied].filter((inst) => {
      if (!CAUSAL_REVEAL_IDS.has(inst.indicatorId)) return false;
      const meta = this._meta.get(inst.instanceId);
      return !!meta && !this._isMarketProfile(meta.def);
    });
  }

  // 当該インスタンスの基底キャッシュ有無（replay.js が per-step 計算のスキップ判定に使う）。
  hasRevealFor(instanceId) {
    return this._revealCache.has(instanceId);
  }

  // 基底の構築が必要か（時間足切替・指標追加・params 変更後の初回フレームで true）。
  revealNeedsBuild() {
    return this._revealTargets().some((inst) => !this._revealCache.has(inst.instanceId));
  }

  // 基底キャッシュ全破棄（時間足切替時に replay.js が呼ぶ）。
  clearRevealCache() {
    this._revealCache.clear();
    this._revealEpoch += 1;
  }

  // 当該インスタンスの基底を破棄（params/variant 変更で陳腐化したとき）。
  _invalidateReveal(instanceId) {
    if (this._revealCache.delete(instanceId)) {
      this._revealEpoch += 1;
    }
  }

  // 基底構築: 対象の未キャッシュ指標を全レンジ（untilTime=tEnd・limit=totalBars＝candles 全本数）で
  //   1 回計算してキャッシュする。per-step（limit=bar+1）と同じ左端 candles[0] 起点の窓＝各バー値が
  //   完全一致（実測ゲート）。失敗した指標はキャッシュされず per-step へフォールバック（次フレーム再試行）。
  async buildRevealBase(tEnd, totalBars) {
    const epoch = this._revealEpoch;
    const targets = this._revealTargets().filter((inst) => !this._revealCache.has(inst.instanceId));
    await Promise.all(targets.map(async (inst) => {
      const meta = this._meta.get(inst.instanceId);
      const params = this._paramsObject(inst.params);
      const variant = inst.variant ?? this._defaultVariant(meta.def);
      try {
        // ISSUE-288: 本経路も他の送信経路と同一の規約に従う。以前はここだけが
        //   `computeTimeframe`（計算.時間足）を載せず、variant スコープも掛けずに送っていた。
        //   その結果、上位足計算の指標がチャート足で計算され、確定時に描いた投影済みの階段を
        //   上書きして「上位足指標が消える」ように見えた（実 UI 実測）。
        const result = await this._compute.compute({
          indicatorId: inst.indicatorId,
          variant,
          params: this._scopedParams(inst.indicatorId, variant, params),
          computeTimeframe: this._calcTimeframeOf(params),
          datasetRef: this._datasetRef,
          generation: 0,
          timeframe: this._timeframe,
          limit: totalBars,
          mode: 'full',
          untilTime: tEnd,
        });
        if (epoch !== this._revealEpoch) {
          return;   // 構築中に無効化された（時間足切替等）＝遅延応答を破棄
        }
        const series = this._validateSeriesNames(result.series ?? [], meta.def, params);
        const times = new Map();
        for (const p of series) {
          if (Array.isArray(p.data)) {
            times.set(p.name, p.data.map((pt) => pt.time));
          }
        }
        this._revealCache.set(inst.instanceId, { def: meta.def, params, series, times });
      } catch (err) {
        if (typeof console !== 'undefined' && console.warn) {
          console.warn('reveal 基底計算失敗（per-step へフォールバック）:', inst.indicatorId, err && err.message);
        }
      }
    }));
  }

  // 同期リビール描画: キャッシュ系列を t 以下へスライスし、既存の描画経路（_renderInstance＝
  //   remove(keepPane)+redraw）で反映する。await を挟まない＝足リビールと同一同期ブロックで呼べる
  //   （完成足チラ見せ防止の不変条件を保つ）。horizontal_line はスライス対象外（t 不変を実測済み）。
  revealTo(t) {
    for (const [instanceId, cache] of this._revealCache) {
      const inst = this._state.applied.find((i) => i.instanceId === instanceId);
      if (!inst) {
        continue;   // 削除済みインスタンス（描画しない・掃除は removeInstance が担う）
      }
      const series = cache.series.map((p) => {
        const ts = cache.times.get(p.name);
        if (!Array.isArray(p.data) || !ts) {
          return p;
        }
        return { ...p, data: p.data.slice(0, upperBound(ts, t)) };
      });
      this._renderInstance({
        instanceId,
        accepted: true,
        def: cache.def,
        params: cache.params,
        wantLatest: false,
        series,
        hidden: !inst.visible,
      });
    }
  }

  // ================= [足内一括計算・ISSUE-232] =================
  //   再生中の足内更新は、従来は 1 ティックごとに /compute を往復していた（実測 ~100ms 遅延＋
  //   throttle＝ローソクだけ先に動いて指標が遅れて追いつく）。本経路は「そのバーの足内推移の
  //   各時点」をバー開始前に一括計算しておき、描画時は計算済み値を同期反映するだけにする
  //   （＝ローソクと同一同期ブロック＝遅延ゼロ）。計算は replay.js が先読みで発行する。

  // 足内追従の対象（適用済み ∩ 共有 INTRABAR_FORMING_IDS）。一括計算の要求単位でもある。
  //   MP は /compute を持たないため対象外（_isMarketProfile で除外）。
  //   **上位足計算（計算.時間足 ≠ チャート）も対象外**（ISSUE-288）: 足内一括計算は
  //   チャート足の窓で計算する経路であり、上位足へ投影できない。対象に含めると、確定時の
  //   full 計算で描いた**投影済みの階段をチャート足の値で上書きしてしまい、上位足指標が
  //   消えたように見える**（実測: 1D 計算の EMA が 5m 値で描かれ、段が消失）。
  //   除外した指標は足内では動かず、バー確定の full 再計算で追いつく（ライブ側 ISSUE-274 の
  //   「段全体を毎 tick 動かすのは費用に見合わない」と同じ判断）。
  formingSeqTargets() {
    return [...this._state.applied].filter((inst) => {
      if (!INTRABAR_FORMING_IDS.has(inst.indicatorId)) return false;
      const meta = this._meta.get(inst.instanceId);
      if (!meta || this._isMarketProfile(meta.def)) return false;
      return !this._usesHigherTimeframe(inst);
    }).map((inst) => {
      const variant = inst.variant ?? this._defaultVariant(this._meta.get(inst.instanceId).def);
      return {
        instanceId: inst.instanceId,
        indicatorId: inst.indicatorId,
        variant,
        // variant スコープ（ISSUE-278 #8）: 本経路も /compute の呼出規約で計算されるため、
        //   その variant の add_* が受理しない param を載せない（載せると validation エラー）。
        //   実 UI で検出: profit_hlband variant=overlay の draw_levels を積んで 500 になっていた。
        params: this._scopedParams(inst.indicatorId, variant, this._paramsObject(inst.params)),
      };
    });
  }

  // 上位足計算（計算.時間足）を使うインスタンスか。'chart'・未指定・チャート足と同値は false。
  //   判定はサーバへ送る値（params.timeframe）と同じものを見る＝送信と足内対象が食い違わない。
  _usesHigherTimeframe(inst) {
    const tf = this._paramsObject(inst.params).timeframe;
    return !!tf && tf !== 'chart' && tf !== this._timeframe;
  }

  // 一括計算済みの 1 ステップを同期描画する。step は { instanceId: series } の写像。
  //   描画実体は末尾差分（_drawLatest＝従来の足内更新と同一経路）。await を挟まないため
  //   呼び出し側はローソク更新と同一同期ブロックで呼べる（＝同時に動く）。
  //   削除済みインスタンス・未知系列は無視する（描画しない）。
  applyFormingStep(step) {
    if (!step) {
      return;
    }
    for (const [instanceId, series] of Object.entries(step)) {
      const inst = this._state.applied.find((i) => i.instanceId === instanceId);
      const meta = this._meta.get(instanceId);
      if (!inst || !meta || !Array.isArray(series)) {
        continue;
      }
      const params = this._paramsObject(inst.params);
      this._drawLatest(instanceId, meta.def, this._validateSeriesNames(series, meta.def, params), params);
    }
  }

  // [reveal] untilTime を設定（以降の再計算がこの時点で計算される＝ライブ同一・df[:t+1]）。
  setUntilTime(t) {
    this._untilTime = t;
  }

  // [reveal] 形成中バー（暫定 OHLC）を設定。undefined で解除（確定足計算へ戻す）。
  setForming(forming) {
    this._forming = forming;
  }

  // [ISSUE-238] 足内窓（winStart/winEnd）を設定。サーバは形成中バーの `to` とこの窓から
  //   「その時点までに到来した実 tick 数」を数えて volume にする。undefined で解除。
  setFormingWindow(win) {
    this._winStart = win ? win.winStart : undefined;
    this._winEnd = win ? win.winEnd : undefined;
  }

  // 共有ベースの compute リクエスト seam を実装: untilTime/forming/足内窓を素通しする。
  //   undefined は compute_http_client の `!== undefined` gate で不送信＝ライブ扱い（後方互換）。
  _extraComputeFields() {
    return {
      untilTime: this._untilTime, forming: this._forming,
      winStart: this._winStart, winEnd: this._winEnd,
    };
  }

  // [reveal] 足内更新: 形成中バーを差し込み、登録指標（共有 INTRABAR_FORMING_IDS）の末尾点のみ
  //   latest 差分再計算する。実体は基底 recomputeFormingTails（forceTail 差分＝ライブと同一機構・
  //   2026-07-22 統一設計）へ委譲し、本メソッドは forming seam（素通し・解除）だけを担う。
  //   forming 解除は finally で必ず行い、後続の確定足計算に forming を残さない。
  async recomputeFormingLatest(forming, win = null) {
    this.setForming(forming);
    this.setFormingWindow(win);
    try {
      await this.recomputeFormingTails();
    } finally {
      this.setForming(undefined);                          // 確定計算へ forming を残さない
      this.setFormingWindow(null);                         // 窓も残さない（確定計算はライブ扱い）
    }
  }

  // MP アクターへ渡す取得 params。全モード機能化により全 param（mode/resmode/range/src/bins/va）を
  //   基底 _mpParams（present の rich 実装）で組み立てる。基底は _paramsObject 済みの平坦 params を受けるため、
  //   subclass の _paramsObject（配列/オブジェクト両受理）で正規化してから委譲する（mode-aware 駆動が有効化）。
  _mpParams(params) {
    return super._mpParams(this._paramsObject(params));
  }

  // MP 設定変更: /compute へ流さず setParams + 現在バー T（_untilTime）で再 enterBar（gear onApply 経路）。
  //   present は recomputeInstance で MP 分岐を持たない（present は _onGearMarketProfile 経路）ため、
  //   本 subclass で MP を enterBar 経路へ委譲し MP が /compute へ流出しないようにする。
  async recomputeInstance(instanceId, newVariant, newParams, opts = {}) {
    const meta = this._meta.get(instanceId);
    if (meta && this._isMarketProfile(meta.def)) {
      return this._recomputeMarketProfile(instanceId, newParams);
    }
    // [reveal 一括・ISSUE-158 ②] params/variant 変更（gear 等）は基底を陳腐化させる→破棄
    //   （次フレームの revealNeedsBuild が再構築）。足内更新の latest 差分は基底に影響しない
    //   （末尾点のみ・確定系列は不変）ため破棄しない。
    if (opts.mode !== 'latest') {
      this._invalidateReveal(instanceId);
    }
    return super.recomputeInstance(instanceId, newVariant, newParams, opts);
  }

  // MP 設定変更（gear onApply）: /compute を呼ばず state.params を更新し、actor へ setParams +
  //   mode-aware で再駆動する。setParams が _applyMode で排他モードを遷移させた後、ticklive は push
  //   （enterBar で base 取り直し・現在バー T=_untilTime）、normal/sessions/replay は as-of refresh
  //   （getContext().to=T で as-seen-at-t 再取得）へ振り分ける。未注入時は state 更新のみ。
  async _recomputeMarketProfile(instanceId, newParams) {
    const meta = this._meta.get(instanceId);
    const params = newParams ?? this._defaultParams(meta.def);
    this._state = this._withParams(this._state, instanceId, params);
    if (this._marketProfile) {
      if (typeof this._marketProfile.setParams === 'function') {
        this._marketProfile.setParams(this._mpParams(params));
      }
      // Phase5（統一成長）: reveal は常に成長状態（growing=true）。setParams（mode 遷移で growing リセット）の
      //   後に growing を再適用し、mode を維持したまま成長軸を確定する（present の _applyMpGrowth と同型）。
      //   mpGrowthResolver（composition root で ()=>true 注入）未注入時は no-op（byte 不変）。
      this._applyMpGrowth();
      // 成長軸ゲート（isGrowingPush＝normal/replay+growing）で push 系（enterBar）へ、sessions/非成長は
      //   refresh へ振り分ける（Phase5: 旧 isTicklive() 表示モードゲートから成長軸へ移行）。
      const push = typeof this._marketProfile.isGrowingPush === 'function'
        && this._marketProfile.isGrowingPush();
      if (push && this._untilTime != null
          && typeof this._marketProfile.enterBar === 'function') {
        await this._marketProfile.enterBar(this._untilTime); // push 成長: base 取り直し（現在バー T）。
      } else if (typeof this._marketProfile.refresh === 'function') {
        await this._marketProfile.refresh(); // sessions/非成長: as-of-T 再取得（因果）。
      }
    }
    this._persistAll();
    this._renderLegend();
    return true;
  }

  // UC-04 表示/非表示。MP は renderer を持たず actor.setEnabled(visible) へ委譲する（present の MP 専用
  //   ハンドラ _toggleMarketProfileVisible を再利用）。非 MP は共有ベースへ委譲する。
  toggleVisible(instanceId) {
    const inst = this._state.applied.find((i) => i.instanceId === instanceId);
    const def = inst ? this._catalog.get(inst.indicatorId) : null;
    if (inst && this._isMarketProfile(def)) {
      return this._toggleMarketProfileVisible(inst);
    }
    return super.toggleVisible(instanceId);
  }

  // UC-05 削除。MP は renderer.remove を持たず actor.setEnabled(false)+detach（あれば）へ委譲する
  //   （present の MP 専用ハンドラ _removeMarketProfile を再利用）。非 MP は共有ベースへ委譲する。
  removeInstance(instanceId) {
    // [reveal 一括・ISSUE-158 ②] 基底キャッシュも掃除（MP は元々エントリ無し＝no-op）。
    this._invalidateReveal(instanceId);
    const inst = this._state.applied.find((i) => i.instanceId === instanceId);
    const def = inst ? this._catalog.get(inst.indicatorId) : null;
    if (inst && this._isMarketProfile(def)) {
      return this._removeMarketProfile(inst);
    }
    return super.removeInstance(instanceId);
  }
}
