// series_primitive_lifecycle.js — lwc ISeriesPrimitive の**ライフサイクル定型**だけを持つ基底
//   （ISSUE-479 Wave2b J-6）。
//
// なぜ在るか:
//   `attached({chart,series,requestUpdate})` / `detached()` / `paneViews()` / 再描画要求 /
//   単一 paneView の保持は、lwc がプラグインへ要求する**定型**であって業務規則ではない。
//   この定型は pair_primitive_base.js・tickvol_bands_primitive.js・price_level_lines_primitive.js
//   の 3 か所へ手書きで複製されていた。price_level_lines_primitive.js:11-16 は複製の理由として
//   「pair_primitive_base はペア固有の状態（_pairs / _highlight / setPairs / setHighlight）と
//   一体化しており、継承すると意味の無い公開面が生える（ISP/LSP）」ことを挙げ、
//   「ライフサイクルだけの基底を新設する案は承認事項として別途提案する」と保留していた。
//   本ファイルがその承認済みの新設であり、ペア固有の状態は PairPrimitiveBase 側に残る。
//
// 公開契約（サブクラスへ継承される不変条件）:
//   - attached({chart,series,requestUpdate}) / detached() で座標源を授受する。
//   - paneViews() は単一 paneView を返し、その renderer().draw(target) が _draw(target) を呼ぶ。
//   - _update() は lwc へ再描画を要求する（attach 前は no-op＝座標源が無く描けないため）。
//   - _draw(target) はサブクラスが override する描画フック（基底は no-op）。
//
// zOrder は**サブクラスの宣言フック**である。paneView に zOrder キーを常時生やすと、宣言して
//   いない primitive の paneView の形が抽出前と変わる（lwc は `'zOrder' in paneView` ではなく
//   関数の有無で分岐する実装が上流バージョンによって在り得る）。抽出は形まで不変でなければ
//   ならないため、`_zOrder()` を定義したサブクラスにだけキーを生やす。
//
// 本ファイルは upstream(lightweight-charts) の API を 1 つも呼ばない（受け取った値を保持し、
//   渡された関数を呼ぶだけ）。したがって @upstream-isolation の申告対象ではない
//   ——同じ理由で pair_primitive_base.js も申告していない（実測: upstream_isolation_declaration
//   の staleIsolationUnits が「呼ばないのに申告している」ファイルを落とす）。

export class SeriesPrimitiveLifecycle {
  constructor() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
    // pane view（renderer を返す）。draw が現在の状態を読むため単一インスタンスで足りる。
    this._paneView = { renderer: () => ({ draw: (target) => this._draw(target) }) };
    // zOrder はサブクラスが `_zOrder()` を宣言したときだけ生やす（キーの有無まで抽出前と一致）。
    //   プロトタイプ鎖は super() の時点で既にサブクラスのものなので、ここで宣言を観測できる。
    if (typeof this._zOrder === 'function') {
      this._paneView.zOrder = () => this._zOrder();
    }
  }

  // lwc が attach 時に chart/series/requestUpdate を供給する。
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

  paneViews() {
    return [this._paneView];
  }

  // lwc へ再描画を要求（attach 前は no-op）。
  _update() {
    if (typeof this._requestUpdate === 'function') {
      this._requestUpdate();
    }
  }

  // 描画フック。サブクラスが override する（基底は no-op）。
  _draw(_target) {}
}
