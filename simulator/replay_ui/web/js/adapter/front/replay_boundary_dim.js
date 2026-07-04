// replay_boundary_dim.js — 期間プリセットの境界（再生区間の開始位置）より過去側の背景を
//   減光する lwc カスタムプリミティブ。プロト web/js/replay_boundary_dim.js を挙動 bit 忠実に移植。
//
// 隔離規約: 本番フロント（adapter/front）は無改変。再生ドライバ（ReplayView）から
//   mainSeries.attachPrimitive で装着するだけ（pair_lines_primitive と同じ装着規約）。
//   chart_renderer 同様、lwc v5 API に触れる「隔離点」（primitive 契約由来の既知例外）。
//   zOrder='bottom'（系列の下＝背景側）に boundaryTime より左の矩形を塗る＝ローソクは上に残る。
//
// 色: 本番背景 #131722 の各 RGB を -10 した #090d18（明度 -10）。boundaryTime=null は塗らない。

const BG_DIM_COLOR = '#090d18'; // 本番背景 #131722 の各チャネルを -10（明度 -10）。

export class ReplayBoundaryDimPrimitive {
  constructor() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
    this._boundaryTime = null; // 再生区間の開始足の time（これより左を減光）。null=減光なし。
    // zOrder='bottom' で系列の下に描く＝背景のみ減光しローソクは原色のまま上に残る。
    this._paneView = {
      renderer: () => ({ draw: (target) => this._draw(target) }),
      zOrder: () => 'bottom',
    };
  }

  // lwc が attach 時に chart/series/requestUpdate を供給する（pair_primitive_base と同契約）。
  attached({ chart, series, requestUpdate }) {
    this._chart = chart;
    this._series = series;
    this._requestUpdate = requestUpdate;
  }

  detached() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  // 減光境界（再生区間の開始足 time）を設定し再描画要求。null で減光解除。
  setBoundaryTime(time) {
    this._boundaryTime = time;
    if (typeof this._requestUpdate === 'function') {
      this._requestUpdate();
    }
  }

  paneViews() {
    return [this._paneView];
  }

  _draw(target) {
    if (this._boundaryTime == null || !this._chart || !this._series) {
      return; // 全期間（境界なし）・attach 前は描画しない。
    }
    const timeScale = this._chart.timeScale && this._chart.timeScale();
    if (!timeScale || typeof timeScale.timeToCoordinate !== 'function') {
      return;
    }
    const xMedia = timeScale.timeToCoordinate(this._boundaryTime);
    if (xMedia == null) {
      return; // 境界足がまだ表示データに無い（playhead 未到達）等は減光しない。
    }
    // timeToCoordinate は足の「中心」x を返すため、そのまま塗ると境界足が中心で2分割される。
    //   ローソク本体の右側（スロット右端＝中心＋barSpacing/2）まで塗り、境界足を丸ごと過去側に含める。
    const barSpacing = (typeof timeScale.options === 'function' && timeScale.options().barSpacing) || 0;
    const xEdge = xMedia + barSpacing / 2;
    target.useBitmapCoordinateSpace((scope) => {
      // 境界右端 x（media）を bitmap 座標へ換算し [0, 境界右端) を塗る。可視域でクランプ。
      const right = Math.max(0, Math.min(scope.bitmapSize.width, xEdge * scope.horizontalPixelRatio));
      if (right <= 0) {
        return; // 境界が左端より左＝可視域すべて「再生区間以降」なら減光なし。
      }
      const ctx = scope.context;
      ctx.fillStyle = BG_DIM_COLOR;
      ctx.fillRect(0, 0, right, scope.bitmapSize.height);
    });
  }
}
