// domain モデル（SeriesDef / AppliedInstance / IndicatorDef）の JS 移植。
//
// Python series_def.py / applied_instance.py / indicator_def.py に対応。
// DOM/chart/fetch 非依存の純ロジック。検索（§4.6）・generation 不変ルール（§6.6）を
// domain に集約する。

import { ColorRole, isColorRole } from './color_roles.js';

export const SeriesKind = Object.freeze({
  LINE: 'line',
  HISTOGRAM: 'histogram',
  HORIZONTAL_LINE: 'horizontal_line',
  // ローソク足の幅に合わせた水平ダッシュ（1 バー 1 本・バー間を繋がない）。
  //   バー毎に確定する水準（例: 1 期先予測区間の上下端）を、傾きという誤った情報を
  //   与えずに並べるための種別。実体は同値 4 値の Candlestick（同事＝水平線 1 本）。
  LEVEL_DASH: 'level_dash',
});

export const LineStyle = Object.freeze({
  SOLID: 'solid',
  DOTTED: 'dotted',
  DASHED: 'dashed',
});

// horizontal_line が必ず持つ色の意味（基本設計_指標カラーテーマ.md §4.1.3 規則 1）。
//   水準線は priceLine 経路で生成され、系列スタイル入口（applySeriesStyle）から構造的に到達
//   できない（E-10）ため、部位ではなく**供給経路そのもの**が意味を一意に決める。よって
//   「宣言し忘れ」も「別トークンの宣言」も起こり得ない不変条件として型側で固定する
//   （18 箇所への手書き反復は、1 箇所の取り残しで規則 1 を破る＝§7.4 段階 1 通過条件 2 を落とす）。
const FORCED_ROLE_BY_KIND = Object.freeze({
  horizontal_line: ColorRole.LEVEL,
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
    colorRole = null,
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
    // colorRole（§4.1）: この系列が伝える「色の意味」。旧 colorRule（色の**値**の席）は撤去した
    //   （§7.2・A-2）。値の席へ意味トークンを入れると、同名の席が状況により色または識別子を保持
    //   することになり置換可能性（LSP）が壊れるため、別名で新設して二重の呼び名を残さない。
    //   語彙外の値は null（未宣言）へ縮退させる＝§5.7 F-C3 を型の入口で確定させ、下流の
    //   resolver に「未知トークン」という状態を持ち込まない。
    this.colorRole = FORCED_ROLE_BY_KIND[kind] ?? (isColorRole(colorRole) ? colorRole : null);
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
