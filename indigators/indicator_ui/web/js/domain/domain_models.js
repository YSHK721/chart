// domain モデル（SeriesDef / AppliedInstance / IndicatorDef）の JS 移植。
//
// Python series_def.py / applied_instance.py / indicator_def.py に対応。
// DOM/chart/fetch 非依存の純ロジック。検索（§4.6）・generation 不変ルール（§6.6）を
// domain に集約する。

export const SeriesKind = Object.freeze({
  LINE: 'line',
  HISTOGRAM: 'histogram',
  HORIZONTAL_LINE: 'horizontal_line',
});

export const LineStyle = Object.freeze({
  SOLID: 'solid',
  DOTTED: 'dotted',
  DASHED: 'dashed',
});

// 出力 1 系列（または系列群）の描画宣言（§3.1.2）。
// source_column（値列名 例 pOL_99）と series_name（描画系列名 例 "pOL 99%"）を別保持する。
export class SeriesDef {
  constructor({
    kind,
    sourceColumn,
    seriesName,
    dynamic,
    sourceColumnPattern = null,
    seriesNamePattern = null,
    style = null,
    width = null,
    colorRule = null,
    priceScaleId = null,
    axisLabelVisible = false,
    pointStyleEditable = false,
    barStyleEditable = false,
  }) {
    this.kind = kind;
    this.sourceColumn = sourceColumn;
    this.seriesName = seriesName;
    this.dynamic = dynamic;
    this.sourceColumnPattern = sourceColumnPattern;
    this.seriesNamePattern = seriesNamePattern;
    this.style = style;
    this.width = width;
    this.colorRule = colorRule;
    this.priceScaleId = priceScaleId;
    this.axisLabelVisible = axisLabelVisible;
    // pointStyleEditable（案A・btlm_trail）: この系列がスタイルタブで「系列表示（ドット/ライン）」を
    //   編集可能かのゲート。既定 false＝未付与系列にはドット/ライン項目を出さない（他指標へ非波及）。
    this.pointStyleEditable = pointStyleEditable;
    // barStyleEditable（案A・btlm_trail_marod）: この系列がスタイルタブで「棒グラフ（histogram）」表示を
    //   選択可能かのゲート。既定 false＝未付与系列には棒項目を出さず、renderer 系列スワップの対象にも
    //   しない（native histogram 他指標を線化しない二重ゲート）。pointStyleEditable と同型・直交。
    this.barStyleEditable = barStyleEditable;
    Object.freeze(this);
  }
}

// チャート上に追加された 1 インスタンス（§3.1.4）。
// generation の単調増加と accepts（現行世代の応答のみ採用）を不変ルールとして集約する。
// styles（ISSUE-109）: 系列名 -> { color?, width?, style?, visible? } のユーザー上書き（差分のみ保持・
//   null=上書きなし）。実描画既定は compute ペイロード由来のため、ここには変更分だけを持つ。
export class AppliedInstance {
  constructor({ indicatorId, variant, params, visible, generation, seq, createdAt, styles = null }) {
    this.indicatorId = indicatorId;
    this.variant = variant;
    this.params = params;
    this.visible = visible;
    this.generation = generation;
    this.seq = seq;
    this.createdAt = createdAt;
    this.styles = styles;
    this.instanceId = `${indicatorId}#${seq}`;
    Object.freeze(this);
  }

  // generation を +1 した新インスタンスを返す（不変のため複製・単調増加）。
  nextGeneration() {
    return new AppliedInstance({
      indicatorId: this.indicatorId,
      variant: this.variant,
      params: this.params,
      visible: this.visible,
      generation: this.generation + 1,
      seq: this.seq,
      createdAt: this.createdAt,
      styles: this.styles,
    });
  }

  // 応答 generation が現行と一致する時のみ採用（§6.6 レース対策）。等値判定（== 相当）。
  accepts(responseGeneration) {
    return responseGeneration === this.generation;
  }
}

// レジストリ 1 件の統一メタデータ（§3.1.3）。
export class IndicatorDef {
  constructor({ id, displayNameKey, category, tab, placement, params, series, compute, descriptionKey = null }) {
    if (!series || series.length === 0) {
      throw new Error(`IndicatorDef '${id}' は series を 1 件以上持つ必要がある`);
    }
    this.id = id;
    this.displayNameKey = displayNameKey;
    this.category = category;
    this.tab = tab;
    this.placement = placement;
    this.params = params;
    this.series = series;
    this.compute = compute;
    this.descriptionKey = descriptionKey;
    Object.freeze(this);
  }

  // 検索一致（§4.6）: 表示名+id を対象、小文字化、部分一致、複数語は論理積。
  // 空クエリは全件通過（語が無い＝論理積は真）。
  matches(query, displayName) {
    const haystack = `${displayName} ${this.id}`.toLowerCase();
    return query
      .toLowerCase()
      .split(/\s+/)
      .filter((t) => t.length > 0)
      .every((term) => haystack.includes(term));
  }
}
