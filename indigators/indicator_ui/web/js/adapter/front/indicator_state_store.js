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
//   field : _persistence / _state / _catalog / _meta / _datasetRef / _renderer / _mp
//           _timeframe（read のみ。確定は _commitTimeframe 経由＝所有者は TimeframeController）
//           _loadCandles / _timeframeObserver
//   method: _commitState / _commitLastSeries / _commitTimeframe / _paramsObject / _isMarketProfile /
//           _gatewayAdapter / _draw / _renderLegend / _renderDialogList / _syncTimeframeButtons
//
// ★ upstream JS の系列追加系 API は一切参照しない（renderer 経由のみ・§2.2 隔離）。

import { deserialize } from '../../usecase/facade.js';

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

  async _restoreRun() {
    const host = this._host;
    const json = {
      applied: host._persistence.loadApplied(),
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
    // 各 instance を再計算して再描画（A方式は variant 事前計算データで復元）。
    for (const inst of appliedList ?? []) {
      const def = host._catalog.get(inst.indicatorId);
      if (!def) {
        continue;
      }
      host._meta.set(inst.instanceId, { def });
      // MP 種別は /compute で計算しようとして失敗させない。復元は MP 側協働子へ委譲する
      //   （保存 params を actor へ渡し、可視だった場合のみ有効化して再取得・表示・ISSUE-094 🔴-4）。
      if (host._isMarketProfile(def)) {
        await host._mp.restoreInstance(inst);
        continue;
      }
      try {
        const gateway = host._gatewayAdapter(inst.variant);
        // B方式は保存 params で再計算（実反映）。A方式は params 無視で id:variant キー解決。
        const restoreParams = host._paramsObject(inst.params);
        const result = await gateway.compute({ indicatorId: inst.indicatorId, variant: inst.variant, params: restoreParams, datasetRef: host._datasetRef, generation: inst.generation });
        host._commitLastSeries(result.series);
        host._draw(inst.instanceId, def, result.series, restoreParams);
        if (!inst.visible) {
          host._renderer.setVisible(inst.instanceId, false);
        }
      } catch {
        // 事前計算未収録 variant はスキップ（A方式制限）。
      }
    }
  }
}
