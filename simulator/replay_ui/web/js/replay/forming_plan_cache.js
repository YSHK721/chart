// forming_plan_cache.js — 足内一括計算の「計画」を所有するロール（ISSUE-256・SRP／ISSUE-232 の実体）。
//
// 設計入力（ISSUE-256）: `setupReplay` に、足内ティック列の取得（buildStream）・計画の署名・構築・
//   先読み・受け取りが、再生駆動や DOM 配線と同じスコープで同居していた。変更要求の出所は
//   「足内の値をどう先読みするか」だけであり、再生テンポや描画とは独立している。
//
// 状態も一緒に移す（ISSUE-181 と同じ方針）: `seqClient` / `planCache` / `planInFlight` は本クラスが所有する。
//
// **速度の不変条件（最重要・抽出前と同一）**: 計画は決して await しない。使う時点で出来ていなければ
//   呼び出し側がその場で従来経路へ落とす。よって再生が計画待ちで遅くなることはない。
//
// 依存は注入する（DOM/lwc 非依存）:
//   fetchImpl / datasetRef        : /intraday 取得
//   seqClient                     : /compute（mode=latest_seq）一括計算クライアント
//   controller                    : formingSeqTargets（対象指標）・limit の基準
//   getCandles / getTimeframe     : 現在の再生対象（窓と署名の材料）
import { intrabarWindow, buildStreamFromResponse } from './stream.js';
import { sampleIndices, formingStatesAt, planSignature } from './forming_plan.js';

export class FormingPlanCache {
  constructor({ fetchImpl, datasetRef, seqClient, controller, getCandles, getTimeframe }) {
    this._fetch = fetchImpl;
    this._datasetRef = datasetRef;
    this._seqClient = seqClient;
    this._controller = controller;
    this._getCandles = getCandles;
    this._getTimeframe = getTimeframe;

    this._cache = new Map();     // idx -> { mode, tf, sig, prices, secs, steps: Map(i -> {instanceId: series}) }
    this._inFlight = new Set();  // 二重発行の防止（idx）
  }

  invalidate() {
    this._cache.clear();
    this._inFlight.clear();
  }

  // ---- 足内ティック列（MT5 モデリング 5 モード相当） ----
  //   idx を明示で受ける（先読みが「次のバー」の窓を作れるようにするため。現在バー固定だった
  //   旧シグネチャ buildStream(cd, mode) は idx=bar 指定と等価＝窓の算出規則は不変）。
  async buildStream(idx, mode) {
    const candles = this._getCandles();
    const timeframe = this._getTimeframe();
    const cd = candles[idx];
    if (!cd) {
      return { prices: [], secs: [] };
    }
    if (mode === 'open_only' || mode === 'math') {
      return buildStreamFromResponse({ mode, cd }); // fetch 前短絡（窓/取得なし）
    }
    const { winStart, winEnd } = intrabarWindow({
      timeframe, cd, prevCandle: candles[idx - 1] || null, nextCandle: candles[idx + 1] || null,
    });
    // ISSUE-238: 形成中バーの実 tick 数はリプレイ時計 `to`（=tick_secs）を要る。MP tick-live /
    //   実時間再生に加え、足内更新そのものが常時この時計を必要とするため real_ticks では常に要求する。
    const wantSecs = mode === 'real_ticks';
    let url = `/intraday?datasetRef=${encodeURIComponent(this._datasetRef)}&start=${winStart}&end=${winEnd}&mode=${encodeURIComponent(mode)}`;
    if (wantSecs) url += '&secs=1';
    let resp = {};
    try { resp = await (await this._fetch(url)).json(); } catch (_e) { /* noop */ }
    // winStart/winEnd を渡し every_tick/ohlc_1min は合成 dwell secs（窓等分・クライアント合成）を並走取得する。
    //   real_ticks は実 tick_secs のまま（byte 不変・窓は無視）。open_only/math は上で短絡済み。
    return buildStreamFromResponse({
      mode, cd, m1: resp.m1 || [], ticks: resp.ticks || [], secs: resp.tick_secs || [], winStart, winEnd,
    });
  }

  // 現在の指標構成・窓での署名（計画の陳腐化判定）。対象 0 件なら null＝一括計算の出番なし。
  signatureFor(idx) {
    const controller = this._controller;
    if (typeof controller.formingSeqTargets !== 'function') {
      return null;   // 一括計算に対応しない controller（テストの fake 等）＝従来経路
    }
    const targets = controller.formingSeqTargets();
    if (!targets.length) {
      return null;
    }
    const cd = this._getCandles()[idx];
    if (!cd) {
      return null;
    }
    return {
      targets,
      sig: planSignature({ targets, timeframe: this._getTimeframe(), limit: idx + 1, untilTime: cd.time }),
    };
  }

  // 計画の構築（ティック列の取得 → 各時点の指標値を 1 リクエストで一括計算）。
  //   失敗しても例外を投げない（計画なし＝従来経路。再生は止めない）。
  async build(idx, mode) {
    const candles = this._getCandles();
    const timeframe = this._getTimeframe();
    const cd = candles[idx];
    if (!cd || mode === 'math') {
      return null;
    }
    const { prices, secs } = await this.buildStream(idx, mode);
    const base = { mode, tf: timeframe, prices, secs, steps: null, sig: null };
    const sigInfo = this.signatureFor(idx);
    if (!sigInfo || !Array.isArray(prices) || prices.length === 0) {
      return base;   // ティック列だけ先読みできた（それでも 1 往復ぶん速くなる）
    }
    const indices = sampleIndices(prices.length);
    // ISSUE-238: 各時点へリプレイ現在時刻を添え、足内窓も送る（サーバが実 tick 数を数える）。
    const formingSeq = formingStatesAt(cd, prices, indices, secs);
    const { winStart, winEnd } = intrabarWindow({
      timeframe, cd, prevCandle: candles[idx - 1] || null, nextCandle: candles[idx + 1] || null,
    });
    const results = await Promise.all(sigInfo.targets.map((t) => this._seqClient.computeSeq({
      indicatorId: t.indicatorId, variant: t.variant, params: t.params,
      datasetRef: this._datasetRef, timeframe, limit: idx + 1, untilTime: cd.time, formingSeq,
      winStart, winEnd,
    }).catch(() => null)));
    const steps = new Map();
    indices.forEach((i, k) => {
      const byInstance = {};
      sigInfo.targets.forEach((t, ti) => {
        const series = results[ti] && results[ti][k];
        if (series) {
          byInstance[t.instanceId] = series;
        }
      });
      if (Object.keys(byInstance).length) {
        steps.set(i, byInstance);
      }
    });
    return { ...base, steps: steps.size ? steps : null, sig: sigInfo.sig };
  }

  // 先読み（fire-and-forget）。現在バーの再生中に次バーぶんを用意する＝待ち時間を露出させない。
  prefetch(idx, mode) {
    if (!this._getCandles()[idx] || mode === 'math' || this._inFlight.has(idx) || this._cache.has(idx)) {
      return;
    }
    this._inFlight.add(idx);
    this.build(idx, mode)
      .then((plan) => { if (plan) this._cache.set(idx, plan); })
      .catch(() => { /* 計画なし＝従来経路（再生は止めない） */ })
      .finally(() => { this._inFlight.delete(idx); });
  }

  // 使い終えた計画の破棄（メモリ・陳腐化の抑制）。
  drop(idx) { this._cache.delete(idx); }

  // 現在保持している計画の idx 一覧（デバッグ用フック __rpPlans の供給元）。
  keys() { return [...this._cache.keys()]; }

  // 使用時の受け取り。モード・時間足・指標構成（署名）が一致するものだけを採用する。
  //   不一致＝設定が変わった＝計算済み値は誤りなので破棄して従来経路へ落とす。
  take(idx, mode) {
    const plan = this._cache.get(idx);
    if (!plan || plan.mode !== mode || plan.tf !== this._getTimeframe()) {
      return null;
    }
    if (plan.steps) {
      const sigInfo = this.signatureFor(idx);
      if (!sigInfo || sigInfo.sig !== plan.sig) {
        return { ...plan, steps: null };  // ティック列だけ流用（値は従来経路で取り直す）
      }
    }
    return plan;
  }
}
