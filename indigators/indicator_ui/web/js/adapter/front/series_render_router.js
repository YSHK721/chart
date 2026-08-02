// series_render_router.js — 計算結果 series を kind 別の描画経路へ振り分けるロール（ISSUE-181・SRP）。
//
// 設計入力（ISSUE-181）: IndicatorController に同居していた 5 アクターの 1 つが「描画振分」
//   （旧 indicator_controller.js:321-380 の _draw と :607-615 の _drawLatest）だった。変更要求の
//   出所は「描画種別（line / histogram / horizontal_line）と renderer の呼び分け」のみで、
//   compute オーケストレーション・永続化・DOM 配線とは独立している。
//
// 状態も一緒に移す（ISSUE-181 対応方針）: 描画先（renderer ポート）の参照を本クラスが保持する。
//   host の _renderer を毎回読みに行かず、自身の出力ポートとして所有する（同一インスタンスを
//   共有する＝挙動不変。host 側は凡例・可視性トグル等の別アクターで同じ renderer を使う）。
//
// host 契約（SeriesRenderHost）が要求する最小メンバー（すべて read/呼び出しのみ・代入しない）:
//   method: _validateSeriesNames（F3 照合・subclass/テストが差し替える seam）
//           _label（pane 名の表示名解決）
//           _applyStoredStyles（保存済みスタイルの再適用。state 所有者は host のため host が担う）
//           _draw / _drawLatest（renderJob からの再入。subclass override を尊重するため host 経由）
//
// ★ upstream JS の系列追加系 API は一切参照しない（renderer 経由のみ・§2.2 隔離）。

import { seriesKind } from '../../domain/series_kind.js';
import { barStyleEditableFor } from '../../usecase/form_model.js';

// 末尾K差分反映（updateSeriesTail）の対象となる時系列系列か。horizontal_line は末尾K切り
//   せず全件返るため対象外（latest 経路に乗らず remove+redraw へフォールバックする）。
export function isTailUpdatable(payload) {
  return seriesKind(payload.kind).tailUpdatable;
}

export class SeriesRenderRouter {
  constructor(host, renderer) {
    this._host = host;
    // 描画先ポート（本ロールが所有する）。
    this._renderer = renderer;
  }

  // 描画: F3 通過系列を kind 別に renderer へ渡す（line / histogram / horizontal_line）。
  //   params は F3 期待名の動的生成（moving_averages の任意期間）に用いる。
  //   placement='overlay' は価格 pane(0) のローソクへ重畳（バンド等）、'pane' は専用 pane
  //   （v5 ネイティブ・独立価格軸＋指標名＋高さドラッグ）。renderer が pane 生成と水準線配線を担う。
  draw(instanceId, def, series, params = null) {
    const host = this._host;
    const validated = host._validateSeriesNames(series, def, params);
    // kind → 描画経路は series_kind 台帳（renderRoute）で一元化（新種別は台帳追記で完結・OCP）。
    //   単一前進走査で振り分けるため各経路内の順序は従来 filter と同一。未知 kind は非描画。
    const routed = { line: [], histogram: [], horizontal: [], level_dash: [] };
    for (const p of validated) {
      // 案A（btlm_trail_marod）: barStyleEditable 一致系列（front カタログ由来・backend 非関与）へ
      //   bar_editable=true を注入する。renderer はこのヒントで line ⇄ histogram スワップ対象を識別
      //   し保持データを退避する（p.kind を消費する本ループが唯一の front 系列メタ付与点＝同所）。
      //   非一致系列にはキーを付けない（renderer の bar_editable===true ゲートが false のまま＝非波及）。
      if (barStyleEditableFor(def, p.name)) {
        p.bar_editable = true;
      }
      const route = seriesKind(p.kind).renderRoute;
      if (routed[route]) {
        routed[route].push(p);
      }
    }
    const lines = routed.line;
    const histograms = routed.histogram;
    const hlines = routed.horizontal;
    const levelDashes = routed.level_dash;
    const opts = { pane: def.placement !== 'overlay', name: host._label(def) };
    if (histograms.length > 0) {
      this._renderer.renderHistogram(instanceId, histograms, opts);
    }
    if (lines.length > 0) {
      this._renderer.renderLine(instanceId, lines, opts);
    }
    if (levelDashes.length > 0) {
      this._renderer.renderLevelDash(instanceId, levelDashes, opts);
    }
    for (const h of hlines) {
      this._renderer.renderHorizontal(instanceId, h.lines ?? []);
    }
    // ISSUE-109: 保存済みスタイル上書きを再適用する（redraw/restore/時間足切替で系列は
    //   ペイロード既定色で再生成されるため、描画の最後に毎回上書きし直す＝永続反映）。
    host._applyStoredStyles(instanceId);
  }

  // Latest: F3 通過系列の末尾K点を {instanceId}::{name} キーで updateSeriesTail へ差分反映する。
  drawLatest(instanceId, def, series, params = null) {
    const validated = this._host._validateSeriesNames(series, def, params);
    for (const p of validated) {
      if (isTailUpdatable(p)) {
        this._renderer.updateSeriesTail(`${instanceId}::${p.name}`, p.data ?? []);
      }
    }
  }

  // def の全系列が末尾K差分可能（line/histogram のみ・horizontal_line を含まない）か。
  //   latest 要求の可否を「計算前」に def の系列定義から判定する（結果データの kind ではない）。
  //   混在/horizontal 指標は backend が line/histogram を末尾K点へ trim する一方フロントは全差替に
  //   落ちるため、trim 済みデータで全描画＝ライン履歴が 1 点に潰れる。よって最初から full を要求する。
  canTailUpdate(def) {
    const series = def?.series ?? [];
    if (series.length === 0) {
      return false;
    }
    return series.every(isTailUpdatable);
  }

  // 描画フェーズ（同期）: 退避済み job の series を renderer へ反映する。await を挟まないため
  //   複数 job を連続実行しても中間ペイントが起きず、全指標が同時に更新される（ISSUE-023）。
  //   実描画は host._draw / host._drawLatest 経由で呼ぶ（subclass override・テスト差し替えを尊重）。
  renderJob(job) {
    const host = this._host;
    if (!job || !job.accepted) {
      return;
    }
    if (job.wantLatest) {
      // Latest: 末尾K点を series.update で差分反映（過去確定足は不変・全描画しない）。
      host._drawLatest(job.instanceId, job.def, job.series, job.params);
    } else {
      // params 変更で系列名が変わりうる（tgp の分位線 btlm_q{N}＝q_low/q_high 依存）ため、
      // setData 差し替えでは改名系列が更新されず古い系列が残留・消失する。remove+redraw で
      // 全系列を現在名で再生成する（line / horizontal_line 共通）。
      // ISSUE-149: keepPane=true で pane を温存（従来の全除去→末尾 addPane は更新のたびに
      // pane が最下段へ移動していた）。redraw は既存 pane（同じ位置）へ再生成される。
      this._renderer.remove(job.instanceId, { keepPane: true });
      host._draw(job.instanceId, job.def, job.series, job.params);
    }
    // 非表示状態を維持（redraw は可視で再生成するため）。
    if (job.hidden) {
      this._renderer.setVisible(job.instanceId, false);
    }
  }
}
