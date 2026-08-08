// indicator_state_store.js — 永続化・復元（UC-07）ロール（ISSUE-181・SRP）。
//
// 設計入力（ISSUE-181）: IndicatorController に同居していた 5 アクターの 1 つが
//   「永続化・復元」（旧 indicator_controller.js:730-802 の _persistAll / restore / _restoreRun /
//   _toJson）だった。変更要求の出所は「保存スキーマと復元手順」のみで、compute オーケストレーション・
//   描画振分・DOM 配線とは独立している。
//
// 状態も一緒に移す（ISSUE-181 対応方針）: 復元実行中の Promise（旧 host._restoreInFlight）は
//   本クラスが所有する。host は inFlight() を参照するだけになる（applyIndicator の競合ガード）。
//
// host 契約（IndicatorStateHost）が要求する最小メンバー（すべて read/呼び出し。フィールドへは代入しない）:
//   field : _persistence / _state / _catalog / _meta / _datasetRef / _renderer
//           _timeframe（read のみ。確定は _commitTimeframe 経由＝所有者は TimeframeController）
//           _loadCandles / _timeframeObserver
//   method: _commitState / _commitLastSeries / _commitTimeframe / _paramsObject / _isMarketProfile /
//           _actorControllerFor（アクター駆動指標の復元先を computeId で解決する）/
//           _gatewayAdapter / _draw / _renderLegend / _renderDialogList / _syncTimeframeButtons
//
// ★ upstream JS の系列追加系 API は一切参照しない（renderer 経由のみ・§2.2 隔離）。

import { deserialize } from '../../usecase/facade.js';
import { isActorDriven } from '../../usecase/actor_driven_ids.js';

// IndicatorStateHost 契約の実体列挙（ISSUE-255）。上のコメントで宣言していた面を**実体**にした。
//   合成時に createHostView(host, 本契約) を渡すため、ここに無いメンバーへ触れると実行時に例外。
//   本協働子（クライアント）が所有するインターフェースであり、host は満たす側（ISP/DIP）。
export const STATE_STORE_HOST_CONTRACT = Object.freeze({
  role: 'IndicatorStateHost',
  methods: Object.freeze([
    '_commitState', '_commitLastSeries', '_commitTimeframe', '_paramsObject',
    '_isMarketProfile', '_actorControllerFor', '_gatewayAdapter', '_draw',
    '_renderLegend', '_renderDialogList', '_syncTimeframeButtons',
  ]),
  fields: Object.freeze([
    '_persistence', '_state', '_catalog', '_meta', '_datasetRef', '_renderer',
    '_timeframe', '_restoreInFlight',
  ]),
  // 合成時の注入次第で不在/ null になりうる面（fresh インスタンスでは未在席）。
  optionalFields: Object.freeze(['_loadCandles', '_timeframeObserver']),
});

export class IndicatorStateStore {
  constructor(host) {
    this._host = host;
    // ISSUE-153: 復元実行中の Promise（null=非実行）。applyIndicator が完了待ちに使う。
    this._restoreInFlight = null;
  }

  // 復元実行中の Promise（非実行時 null）。host の applyIndicator が競合ガードに参照する。
  inFlight() { return this._restoreInFlight; }

  // 復元中 Promise の注入（host の互換アクセサ経由。所有者は本クラスのまま）。
  setInFlight(value) { this._restoreInFlight = value; }

  // UC-07 永続化。
  persistAll() {
    const host = this._host;
    host._persistence.saveApplied(host._state.applied.map((i) => this.toJson(i)));
    host._persistence.saveFavorites(host._state.favorites);
    host._persistence.saveUiState(host._state.uiState);
  }

  toJson(i) {
    return {
      instanceId: i.instanceId,
      indicatorId: i.indicatorId,
      variant: i.variant,
      params: i.params,
      visible: i.visible,
      generation: i.generation,
      seq: i.seq,
      createdAt: i.createdAt,
      styles: i.styles ?? null,
    };
  }

  // ISSUE-153: restore は保存状態で _state を丸ごと置換するため、読込直後〜復元完了の間に
  //   applyIndicator された指標が state から消え「描画だけ残る孤児」になる（以後どの再計算にも
  //   乗らず凍結＝『ライブで btlm_trail が更新されない』の真因）。復元中フラグを公開し、
  //   applyIndicator 側が完了を待つことで競合を排除する。
  async restore() {
    const run = this._restoreRun();
    this._restoreInFlight = run;
    try {
      await run;
    } finally {
      this._restoreInFlight = null;
    }
  }

  /**
   * 保存済み applied から、**カタログに存在しない indicatorId** の instance を落とす。
   *
   * 在席の権威はカタログ（`_catalog.get`）1 つである。指標を UI から外した後も保存状態には
   * その instance が残るため、除去しないと `_renderLegend` が `_state.applied` をそのまま
   * 描いて「系列もデータも無い凡例行」が残る（`rebuildApplied` は def 無しを compute/描画から
   * 除外するが、状態からは落とさない＝状態とカタログの不整合が残るのが真因）。
   * 落とした結果は永続化にも書き戻し、次回起動でゴミが再登場しないようにする。
   */
  _pruneUnknown(instances) {
    const host = this._host;
    const list = Array.isArray(instances) ? instances : [];
    const kept = list.filter((inst) => !!host._catalog.get(inst?.indicatorId));
    // ISSUE-281: **カタログに存在しない param 名**も落とす（在席の権威はカタログ 1 つ、という
    //   同じ規約を param 粒度へ広げる）。サーバは ISSUE-278 #8 でフェイルクローズ化されており、
    //   廃止 param が保存状態に残っていると `/compute` が常に 400 になり、その指標は
    //   **永久に計算できなくなる**（実測: profit_rsi の `ma_period`）。送信側の絞り込み
    //   （catalog.scopedParams の許可リスト）だけでは保存状態が汚れたままなので、ここで治す。
    //   variant が今受理しない param は落とさない（variant を戻せば有効＝定義には在る）。
    const healed = kept.map((inst) => this._pruneUnknownParams(inst));
    const changed = kept.length !== list.length
      || healed.some((inst, i) => inst !== kept[i]);
    if (changed) {
      host._persistence.saveApplied(healed);
    }
    return healed;
  }

  /** instance の params から、カタログ定義に無い名前を落とす（object 形・[k,v] 配列形の両対応）。
   *
   * **アクター駆動指標（market_profile / tickvol_bands）は対象外**。これらは `/compute` を
   * 通らず、受理集合の権威はアクター側にある。front カタログが宣言していない param（例:
   * market_profile の `bins`）が正当に存在するため、ここで落とすと保存済み設定が失われる
   * （実測: 既存検定「MP restore: … saved params」が Red になった）。
   */
  _pruneUnknownParams(inst) {
    const def = this._host._catalog.get(inst?.indicatorId);
    const params = inst?.params;
    if (!def || !params || isActorDriven(def)) {
      return inst;
    }
    const known = new Set(def.params.map((p) => p.name));
    if (Array.isArray(params)) {
      const kept = params.filter(([name]) => known.has(name));
      return kept.length === params.length ? inst : { ...inst, params: kept };
    }
    if (typeof params !== 'object') {
      return inst;
    }
    const entries = Object.entries(params);
    const kept = entries.filter(([name]) => known.has(name));
    return kept.length === entries.length ? inst : { ...inst, params: Object.fromEntries(kept) };
  }

  async _restoreRun() {
    const host = this._host;
    const json = {
      applied: this._pruneUnknown(host._persistence.loadApplied()),
      favorites: host._persistence.loadFavorites(),
      uiState: host._persistence.loadUiState(),
    };
    host._commitState(deserialize(JSON.stringify({ ...json, seqCounters: {} })));
    // 永続化された時間足を復元（compute は gateway 経由で時間足を注入するため再計算前に確定）。
    //   初期足（constructor 値・composition root が candles 取得済み）と異なる場合のみ candles を再取得。
    const savedTimeframe = host._state.uiState?.timeframe;
    if (savedTimeframe && savedTimeframe !== host._timeframe) {
      host._commitTimeframe(savedTimeframe);
      if (typeof host._loadCandles === 'function') {
        const candles = await host._loadCandles(host._datasetRef, savedTimeframe);
        if (candles && candles.length > 0) {
          host._renderer.setCandles(candles);
        }
      }
    }
    host._syncTimeframeButtons();
    // 復元した時間足を購読者へ通知する（売買マーカーの該当時間足フィルタが restore 後の
    //   現在時間足を正しく評価できるようにする。通知欠落だと該当時間足でも非表示になる逆動作）。
    host._timeframeObserver?.(host._timeframe);
    await this.rebuildApplied(host._state.applied);
    host._renderLegend();
    host._renderDialogList();
  }

  /**
   * 適用済みインスタンス配列を、現在の時間足のまま再計算して再描画する（公開入口）。
   *
   * 基本設計_チャートテンプレート.md v0.1.1 §7.2 S2（承認済み）: `_restoreRun` の
   * 「再構築ループ」部を挙動不変で抽出した唯一の実体。復元（restore）とテンプレート適用
   * （UC-T02 手順 2〜4）が同一手順（MP 復元経路・styles 再適用・個別失敗の局所化）を共有する。
   * 時間足復元・保存状態の読込・凡例/ダイアログ再描画は含まない（呼び出し側の責務）。
   *
   * ★ 事前条件（違反すると styles が黙って失われる）:
   *   呼び出し前に `host._state.applied` へ当該 instance が在席していること。
   *   `_draw` → `series_render_router.js:74` → `indicator_controller.js:305` が
   *   `_state.applied.find` を読んで保存済みスタイルを再適用するため、state 未在席のまま
   *   本入口を呼ぶと描画は成功するが styles 上書きだけが無反映になる（サイレント欠落）。
   *
   * @param {Array} appliedList 再構築対象（AppliedInstance またはその JSON 形の配列）。
   */
  async rebuildApplied(appliedList) {
    const host = this._host;
    // ISSUE-202（起動所要・2026-07-29 実測）: 本ループは `for … await` の完全直列で、宣言順の
    //   先頭に market_profile があるとその `/market_profile`（実測 13.0 秒）が終わるまで後続指標の
    //   compute が 1 件も発行されず、「起動してもチャート上に指標が出ない」時間が指標数と MP の
    //   遅さの和になっていた（実測: 5 件で全指標が出るまで 2.7〜8.3 秒・MP 待ちで最大 14.5 秒）。
    //   恒久対策（ユーザー承認 2026-07-29）:
    //     (a) compute を **並列**発行する。並列安全の前提は ISSUE-165 で恒久是正済み
    //         （series は per-call gateway 捕捉・state は当該行のみマージ）。
    //     (b) MP の復元は **待ち合わせに載せない**（actor が自分の完了時に描画する）。復元の完了は
    //         非 MP 指標で決まり、凡例・ダイアログの再描画が MP の応答待ちで止まらない。
    //   描画（_draw）は **宣言順に直列化**する（下記 drawChain）。pane は初回描画時に生成される
    //   ため、完了順に描くと pane の並びが起動ごとに変わる（ISSUE-149 の並び順保証が壊れる）。
    //   compute は並列・描画は宣言順＝「早い指標から順に、常に同じ並びで」出る。
    const list = [];
    for (const inst of appliedList ?? []) {
      const def = host._catalog.get(inst.indicatorId);
      if (!def) {
        continue;
      }
      host._meta.set(inst.instanceId, { def });
      list.push({ inst, def });
    }
    // (b) MP は起動の待ち合わせから外す（失敗は当該 1 件に閉じる＝F-T4）。
    for (const { inst, def } of list) {
      if (host._isMarketProfile(def)) {
        Promise.resolve(host._actorControllerFor(def).restoreInstance(inst)).catch(() => {
          // MP 復元の失敗は当該 1 件のみスキップ（他指標の復元・描画は継続する）。
        });
      }
    }
    // (a) 非 MP は compute を並列発行し、描画だけを宣言順へ直列化する。
    let drawChain = Promise.resolve();
    const tasks = list
      .filter(({ def }) => !host._isMarketProfile(def))
      .map(({ inst, def }) => (async () => {
        let computed = null;
        try {
          const gateway = host._gatewayAdapter(inst.variant);
          // B方式は保存 params で再計算（実反映）。A方式は params 無視で id:variant キー解決。
          const restoreParams = host._paramsObject(inst.params);
          const result = await gateway.compute({ indicatorId: inst.indicatorId, variant: inst.variant, params: restoreParams, datasetRef: host._datasetRef, generation: inst.generation });
          computed = { result, restoreParams };
        } catch {
          // 事前計算未収録 variant はスキップ（A方式制限）。描画も行わない。
          return;
        }
        // 宣言順の描画ゲート: 自分より前の指標の描画が終わってから描く（pane の並び順を固定）。
        drawChain = drawChain.then(() => {
          try {
            host._commitLastSeries(computed.result.series);
            host._draw(inst.instanceId, def, computed.result.series, computed.restoreParams);
            if (!inst.visible) {
              host._renderer.setVisible(inst.instanceId, false);
            }
          } catch {
            // 描画失敗も当該 1 件に閉じる（後続の描画を止めない）。
          }
        });
        await drawChain;
      })());
    await Promise.allSettled(tasks);
    // 直列化した描画の残り（最後の then）まで待ってから完了する。
    await drawChain;
  }
}
