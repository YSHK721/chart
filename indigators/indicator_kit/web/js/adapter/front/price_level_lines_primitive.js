// price_level_lines_primitive.js — 建値/損切り/利確/ロスカットの水準線を描く
//   カスタム ISeriesPrimitive（ISSUE-368 スライス 4）。
//
// 設計入力: 設計書 §6「Adapter: PriceLevelLinesPrimitive」／§4-B。雛形は pair_lines_primitive.js
//   （attached({chart,series,requestUpdate}) → paneViews() → renderer().draw(target) →
//   target.useBitmapCoordinateSpace(scope => scope.context 描画)、series.priceToCoordinate は
//   範囲外で null、setChromeColors で色を受ける）。
//   `createPriceLine` を使わないのは、`chart_renderer.js:596-598` の `_createPriceLines` が
//   **指標スロット紐付け**専用で流用できないため（実測）。
//
// lwc ライフサイクル（attached/detached/paneViews）の写しは **解消済み**（ISSUE-479 Wave2b J-6）。
//   かつてここには「pair_primitive_base が `_pairs` / `_highlight` / `setPairs` / `setHighlight`
//   というペア固有の状態と一体化しており、そのまま継承すると本 primitive に意味の無い公開面が
//   生える（ISP/LSP）。ライフサイクルだけの基底を新設する案は承認事項として別途提案する」と
//   書いてあった。その承認が下り、ライフサイクル定型だけを持つ基底
//   `series_primitive_lifecycle.js` が新設されたため、本 primitive はそれを継承する。
//   ペア固有の公開面は継承されない（`setPairs` / `setHighlight` は生えない＝ISP/LSP を維持）。
//
// なぜ掴み判定（handleAt）を primitive が持つか:
//   掴める位置は「いま描かれている位置」でなければならない。drag 側で価格→座標を再計算すると
//   描画と掴みで座標源が 2 つになり、スケール変更時にズレる。描画のたびに y 表を更新し、
//   その表だけを掴み判定の根拠にする（単一ソース）。
//
// 色（FR-C13・段階 5-E）: canvas 描画は CSS 変数を解決できないため注入で受ける。
//   **既存スロットのみを使う**（`priceLine`＝建値・`pairLineLoss`＝損切り/ロスカット・
//   `pairLineWin`＝利確）。専用スロットの新設は chrome_tokens.js（CSS 変数・比率検定を伴う
//   共有台帳）の変更＝配色の新規決定であり、承認事項（UI 変更）として別途扱う。
//
// 単体検証は fake target/series で座標・色を観測し、canvas 実描画は実 UI 検証へ委譲する。

import { CHROME_CURRENT } from '../../usecase/chrome_tokens.js';
// タグ不透明度の選定（ISSUE-435 残件 2）に使う色の数学は domain の単一ソースから取る。
//   合成（mixChannels＝8bit 丸め・canvas と同じ階調）とコントラスト比（WCAG 2.x）の
//   第 2 実装をここへ作らない。
import { contrastRatio, mixChannels, normalizeHexColor } from '../../domain/color_value.js';
// ラベルの**表示名と価格書式**は単一ソースから取る（ISSUE-435）。ここへ書き写すと、
//   モーダルの欄・アーム中バー・右クリックの解除項目と同じ表が 4 つ目に増え、
//   ゴーストと線で価格の書式が割れる（ISSUE-368 で実際に起きた症状と同型）。
import { priceOnLine, priceTargetLabel } from './price_format.js';
// lwc ライフサイクル定型（attach・paneView・再描画要求・_draw フック）の単一ソース。
import { SeriesPrimitiveLifecycle } from './series_primitive_lifecycle.js';

// 掴めない線の種別（読み取り専用）。ロスカットは口座状態から導出される結果であって入力ではない。
const READ_ONLY_KINDS = new Set(['losscut']);

// 線種ごとの破線パターン（[] は実線）。ロスカットは「入力ではない」ことを見た目でも区別する。
const DASH = Object.freeze({ entry: [], stop: [], take: [], losscut: [4, 4] });

// 水準線のタグ（ISSUE-435 実装 2・**依頼者裁定 2026-08-21**）。
//
// 参照実装 `marker()`（integrated_position_sizing_calculator.html:773-778）は
//   `9px ui-monospace,monospace` / 項目名は線と同色・価格は灰の **2 段・背景なし**を定義する。
//   これをそのまま写した版は、**実 UI 実測（2026-08-21・ライブ 1600×1000・dpr=1）で
//   ローソク・移動平均・btlm_trail の帯に埋もれて読めなかった**。参照実装の数直線は幅 300px
//   程度の無地キャンバスであり、指標が密集する 1600px のチャート上での可読性を一度も
//   定義していない＝**ここは参照実装の射程外**である。よって裁定で置き換えた:
//     **線と同色で塗った小さなタグ＋抜き文字 1 行**（価格軸のタグ・現在値タグと見た目を揃える）。
//
// 参照実装から離れた点と理由（推測ではなく裁定・実測が根拠）:
//   1. 2 段 → **1 行**（「項目名 価格」）。背景を敷くと 2 段は縦に嵩み、線の間隔を余計に食う。
//   2. 灰の文字 → **抜き文字＝地の色**（`layoutBackground`）。塗りの上で読める色を
//      既存スロットから選ぶ（新スロットの追加は禁止）。選定は実測のコントラスト比による:
//        塗り     priceLine #ff9800 / pairLineWin #26a69a / pairLineLoss #ef5350
//        地の色   #131722 → 8.30 / 5.97 / 5.13（最小 5.13・WCAG AA 4.5 超）
//        白       #ffffff → 2.16 / 3.00 / 3.49（最小 2.16・不可）
//        uiText   #d1d4dc → 1.45 / 2.02 / 2.35（最小 1.45・不可）
//      数値だけでなく**構造**でも地の色が正しい: 線色は「地の上で目立つ色」として選ばれている
//      ので、その線色で塗ったタグを地の色で抜けば、テーマを変えても関係が保たれる
//      （固定の白・固定の黒はテーマ変更で前提が崩れる）。
//   3. 9px → **12px**。価格軸タグ・現在値タグと字送りを揃える（裁定「見た目が揃い」）。
//      12 は lwc の layout 既定 `fontSize:12`（vendor 実測）＝価格軸が実際に使っている値。
//      書体は等幅のまま（参照実装 :775 の定義。数字の桁が揃う）。
const FONT_FAMILY = 'ui-monospace,monospace';
const FONT_PX = 12;
const TAG_PAD_X = 5;               // 文字の左右の余白
const TAG_PAD_Y = 3;               // 文字の上下の余白
const TAG_H = FONT_PX + TAG_PAD_Y * 2;
const TAG_GAP = 2;                 // タグと線・タグとタグのすき間
// 置き場は右端（textAlign='right'）。左は凡例・現在値・読み取り欄で既に混雑しており、
//   右は価格軸に隣接して価格の対応が読みやすい。**価格軸には掛からない**: 本 primitive が
//   描くのは版面の canvas で、価格軸は別の canvas である（実 UI 実測 2026-08-21: 版面は
//   1540px で終わり、軸は 1540px から始まる）。幅の内側に収める限り食い込まない。
const RIGHT_MARGIN_PX = 6;

// タグの不透明度の下限規準（WCAG AA・依頼者裁定 2026-08-21「割るなら報告して止める」）。
const TAG_CONTRAST_FLOOR = 4.5;
// 不透明度の走査刻み。canvas の合成は 8bit なので 1/100 より細かくしても見た目の階調が増えない。
const TAG_ALPHA_STEP = 0.01;

/**
 * 半透明色を地へ合成して不透明 hex にする（全域的・解釈できなければ null）。
 *
 * `rgba(r,g,b,a)` の a を読み、`mixChannels(base, rgb, a)` で 8bit 合成する。不透明色は
 * 正規化だけして素通しする。domain の normalizeHexColor は方針として α を捨てる（§4.7）ため、
 * 「α を合成で消してから domain の 1 形式（hex6）へ落とす」変換をここ 1 か所に置く。
 */
export function flattenColorOverBase(color, base) {
  if (typeof color !== 'string') {
    return null;
  }
  const m = color.match(/^rgba\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*,\s*([\d.]+)\s*\)$/i);
  const rgb = normalizeHexColor(color);
  if (rgb === null) {
    return null;
  }
  if (!m) {
    return rgb;   // α を持たない色はそのまま（rgb()/hex）。
  }
  const alpha = Number(m[1]);
  const baseHex = normalizeHexColor(base);
  if (!Number.isFinite(alpha) || baseHex === null) {
    return null;
  }
  return mixChannels(baseHex, rgb, Math.min(1, Math.max(0, alpha)));
}

/**
 * タグ塗りの「AA を割らない範囲で最も透ける」不透明度を選ぶ（ISSUE-435 残件 2・裁定 2026-08-21）。
 *
 * 決め打ちにしない理由（裁定そのもの）: 下に来る現実の色（地・陽線/陰線・取引密度帯）は
 * テーマで変わる。固定値はテーマ変更で AA を割るか、必要以上に不透明になるかのどちらかに倒れる。
 *
 * 規則: a を 0 から 1 へ TAG_ALPHA_STEP 刻みで走査し、全〈塗り × 下地〉の合成色に対する
 * 抜き文字のコントラスト比の最小値が floor 以上になる**最初の a** を返す（＝定義どおり最小）。
 * 合成色のコントラストは a について単調と仮定しない（mixAtContrast と同じ理由: ランプが
 * 文字色を横切ると比が 1 で底を打つ）ため、早期打切りは「最初に満たした a を返す」だけに留める。
 *
 * 全域性（縮退規則）: 解釈できない色が混ざる・どの a でも floor に届かない場合は **1（不透明）**
 * を返す。不透明は従来（半透明化前）と同一の見た目＝機能を落とさず安全側へ倒す。
 */
export function mostTransparentTagAlpha(inputs) {
  if (!inputs || typeof inputs !== 'object') {
    return 1;
  }
  const { fills, textColor, underlays, floor = TAG_CONTRAST_FLOOR, step = TAG_ALPHA_STEP } = inputs;
  const text = normalizeHexColor(textColor);
  const fillHexes = Array.isArray(fills) ? fills.map((c) => normalizeHexColor(c)) : null;
  const underHexes = Array.isArray(underlays) ? underlays.map((c) => normalizeHexColor(c)) : null;
  if (text === null || !fillHexes || !underHexes
    || fillHexes.some((c) => c === null) || underHexes.some((c) => c === null)
    || fillHexes.length === 0 || underHexes.length === 0) {
    return 1;
  }
  const steps = Math.round(1 / step);
  for (let i = 0; i <= steps; i += 1) {
    const a = i / steps;
    let min = Infinity;
    for (const fill of fillHexes) {
      for (const under of underHexes) {
        min = Math.min(min, contrastRatio(text, mixChannels(under, fill, a)));
      }
    }
    if (min >= floor) {
      return a;
    }
  }
  return 1;
}

export class PriceLevelLinesPrimitive extends SeriesPrimitiveLifecycle {
  /**
   * @param {object} [deps]
   * @param {Function} [deps.computeTagAlpha] 不透明度の選定（既定は mostTransparentTagAlpha）。
   *   注入可能なのは検定（発行回数の Spy）のためで、本番結線は引数なしで生成する。
   */
  constructor({ computeTagAlpha = mostTransparentTagAlpha } = {}) {
    super();
    this._levels = null;
    // 直近の描画で確定した y 座標表（掴み判定の唯一の根拠）。[{ kind, index, y }]
    this._handleYs = [];
    // 配信済みのクロム色（配信前＝台帳の現行値）。setChromeColors だけが書き換える。
    this._entryColor = CHROME_CURRENT.priceLine;
    this._stopColor = CHROME_CURRENT.pairLineLoss;
    this._takeColor = CHROME_CURRENT.pairLineWin;
    this._losscutColor = CHROME_CURRENT.pairLineLoss;
    // タグの抜き文字に使う地の色。**既存スロット**（layoutBackground）だけを使う＝配色の
    //   新規決定をしない（新スロットの追加は承認事項。選定根拠は上の定数の注記）。
    this._tagTextColor = CHROME_CURRENT.layoutBackground;
    // 価格の表示桁（銘柄仕様）。解決できないときは undefined＝参照実装どおり整数表示。
    this._digits = undefined;
    // タグの下に来る現実の色（裁定 2026-08-21: 地・陽線/陰線・取引密度帯）。既存スロットのみ。
    this._underCandleUp = CHROME_CURRENT.candleUp;
    this._underCandleDown = CHROME_CURRENT.candleDown;
    this._underBand = CHROME_CURRENT.tickvolBand;
    // タグ塗りの不透明度（残件 2）。導出は色が変わったときだけ（描画のたびに再導出しない）。
    this._computeTagAlpha = typeof computeTagAlpha === 'function'
      ? computeTagAlpha : mostTransparentTagAlpha;
    this._tagAlpha = 1;
    this._tagAlphaKey = null;
    this._refreshTagAlpha();
    // 直近の線工程で確定した可視線（タグ工程の唯一の座標源・媒体座標）。
    this._tagLines = [];
    // タグ専用 paneView（残件 1・裁定「線は従来のまま・タグだけ前面」）。
    //   線の paneView（基底の単一 view・zOrder 無宣言＝'normal' 既定）はそのままにし、
    //   タグだけを 'top' で描く。vendor は 'top' を同一フレームの最後（系列・指標の上）に描く。
    this._tagsPaneView = {
      renderer: () => ({ draw: (target) => this._drawTagsPass(target) }),
      zOrder: () => 'top',
    };
  }

  // ---- lwc ISeriesPrimitive ライフサイクル ----

  // 座標源を手放したら y 表とタグ素材も捨てる（描いていない線を掴めても・名指しできてもならない）。
  //   ライフサイクルの他の 3 項目（chart/series/requestUpdate の授受）は基底が持つ。
  detached() {
    super.detached();
    this._handleYs = [];
    this._tagLines = [];
  }

  // 基底の paneView（線）にタグ専用 paneView を足す（ISSUE-435 残件 1）。
  //   基底の「単一 paneView」契約から離れるのはタグの zOrder（'top'）が線（無宣言＝既定）と
  //   異なるためで、1 つの view では「線は背面のまま・タグだけ前面」（裁定）を表現できない。
  paneViews() {
    return [...super.paneViews(), this._tagsPaneView];
  }

  // 基底の paneView が呼ぶ描画フック＝**線工程のみ**（タグは _tagsPaneView が 'top' で描く）。
  _draw(target) {
    this._drawLinesPass(target);
  }

  // ---- 状態 ----

  /**
   * 価格の表示桁を受ける（ISSUE-435）。`position_sizing_dialog.setSymbolSpec` と同じ名前・同じ
   * 規約にする（新しい配り方を作らない）。解決点は共有配線の 1 か所で、ここは配られるだけ。
   * @param {{digits:number}|null|undefined} spec 解決できないときは null（＝整数表示のまま）。
   */
  setSymbolSpec(spec) {
    this._digits = spec ? spec.digits : undefined;
    this._update();
  }

  // 水準を差し替えて再描画を要求する（attach 前は要求だけ no-op）。
  //   levels: { direction, entryPrices[], stopPrice, takePrice|null, losscutPrice|null }
  setLevels(levels) {
    this._levels = levels || null;
    this._update();
  }

  // 配信されたクロム色から自分のぶんを取り込む。全域的（§7.3 LSP）: null・非オブジェクト・
  //   非文字列・部分指定のいずれでも例外を投げず、解釈できない指定は現行値を保つ。
  setChromeColors(slots) {
    if (!slots || typeof slots !== 'object') {
      return;
    }
    if (typeof slots.priceLine === 'string') {
      this._entryColor = slots.priceLine;
    }
    if (typeof slots.pairLineLoss === 'string') {
      this._stopColor = slots.pairLineLoss;
      this._losscutColor = slots.pairLineLoss;
    }
    if (typeof slots.pairLineWin === 'string') {
      this._takeColor = slots.pairLineWin;
    }
    if (typeof slots.layoutBackground === 'string') {
      this._tagTextColor = slots.layoutBackground;
    }
    // タグの下地（残件 2 の選定入力）。塗り・文字と同じ全域性（非文字列は現行値を保つ）。
    if (typeof slots.candleUp === 'string') {
      this._underCandleUp = slots.candleUp;
    }
    if (typeof slots.candleDown === 'string') {
      this._underCandleDown = slots.candleDown;
    }
    if (typeof slots.tickvolBand === 'string') {
      this._underBand = slots.tickvolBand;
    }
    this._refreshTagAlpha();
    this._update();
  }

  // タグ不透明度の再導出（色が実際に変わったときだけ発行する＝描画・同値配信では発行しない）。
  //   入力キーが同じなら前回の採用値を保つ。導出そのものは注入された純関数（単一ソース）。
  _refreshTagAlpha() {
    const inputs = {
      fills: [this._entryColor, this._stopColor, this._takeColor, this._losscutColor],
      textColor: this._tagTextColor,
      underlays: [
        this._tagTextColor,   // 地（抜き文字と同じスロット layoutBackground）
        this._underCandleUp,
        this._underCandleDown,
        flattenColorOverBase(this._underBand, this._tagTextColor),
      ],
    };
    const key = JSON.stringify(inputs);
    if (key === this._tagAlphaKey) {
      return;
    }
    this._tagAlphaKey = key;
    this._tagAlpha = this._computeTagAlpha(inputs);
  }

  // ---- 掴み判定 ----

  // 直近の描画で確定した y 表から、許容 px 以内で最も近い掴み対象を返す（無ければ null）。
  //   読み取り専用の線（ロスカット）は対象外。範囲外でスキップされた線も表に載らない
  //   ＝描いていない線は掴めない。
  handleAt(y, tolerancePx) {
    if (!Number.isFinite(y)) {
      return null;
    }
    let best = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const handle of this._handleYs) {
      const distance = Math.abs(handle.y - y);
      if (distance <= tolerancePx && distance < bestDistance) {
        best = handle;
        bestDistance = distance;
      }
    }
    return best ? { kind: best.kind, index: best.index } : null;
  }

  // ---- 描画 ----

  // 公開契約: 1 フレームぶんの全描画（線 → タグ）。単体検定・結線検定が「この target に
  //   何が描かれるか」を 1 呼び出しで観測するための面で、本番（lwc）は paneView ごとに
  //   _drawLinesPass（線・既定 zOrder）→ _drawTagsPass（タグ・'top'）の順で呼ぶ。
  //   lwc は同一フレームで 'top' を最後に描くため、順序はここと同じになる。
  draw(target) {
    this._drawLinesPass(target);
    this._drawTagsPass(target);
  }

  // 線工程: 座標（priceToCoordinate）を発行する唯一の工程。可視線の表（_tagLines）と
  //   掴み判定の y 表（_handleYs）をここで確定させる。タグ工程は再発行せずこの表を使う
  //   （描画・掴み・タグの座標源を 1 つに保つ＝計算量テストが再発行の不在を固定する）。
  _drawLinesPass(target) {
    if (!this._chart || !this._series || !this._levels) {
      return;   // attach 前・水準未設定は座標源が無いので描かない（防御）。
    }
    const lines = [];
    this._handleYs = [];
    for (const spec of this._lineSpecs()) {
      const y = this._series.priceToCoordinate(spec.price);
      if (y == null) {
        continue;   // 可視範囲外はスキップ（pair_lines_primitive.js:73 と同一規約）。
      }
      lines.push({ ...spec, y });
      if (!READ_ONLY_KINDS.has(spec.kind)) {
        this._handleYs.push({ kind: spec.kind, index: spec.index, y });
      }
    }
    this._tagLines = lines;
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const width = this._extentWidth(scope);
      // 媒体（CSS）座標 → 装置ピクセル。`priceToCoordinate` は媒体座標を返すのに対し、
      //   `useBitmapCoordinateSpace` は変換を単位行列へ戻す（vendor 実測: 下の注記）ため、
      //   dpr を掛けないと dpr>1 で線もラベルも半分の位置・半分の大きさになる。
      const vr = scale(scope.verticalPixelRatio);
      for (const line of lines) {
        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = line.color;
        ctx.lineWidth = 1;
        if (typeof ctx.setLineDash === 'function') {
          ctx.setLineDash(DASH[line.kind] || []);
        }
        ctx.moveTo(0, line.y * vr);
        ctx.lineTo(width, line.y * vr);
        ctx.stroke();
        ctx.restore();
      }
    });
  }

  // タグ工程（'top' の paneView が呼ぶ）: 線工程が確定させた可視線をタグにして前面へ描く。
  //   座標を再発行しない（線工程の _tagLines が唯一の座標源）。線工程が一度も走っていない・
  //   detach 済みのときは素材が空＝何も描かない。
  _drawTagsPass(target) {
    if (this._tagLines.length === 0) {
      return;
    }
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const width = this._extentWidth(scope);
      const hr = scale(scope.horizontalPixelRatio);
      const vr = scale(scope.verticalPixelRatio);
      this._drawTags(ctx, this._tagLines, { width, hr, vr });
    });
  }

  // 「項目名 価格」のタグ（ISSUE-435 実装 2・裁定 2026-08-21）。線と同色で塗り、地の色で抜く。
  //   文字・塗りを描けない描画文脈（最小 fake・後方互換）は線だけ描いて黙って抜ける
  //   （`setLineDash` の既存ガードと同じ態度＝例外を投げない）。
  _drawTags(ctx, lines, { width, hr, vr }) {
    if (typeof ctx.fillText !== 'function' || typeof ctx.fillRect !== 'function' || lines.length === 0) {
      return;
    }
    ctx.save();
    ctx.font = `${FONT_PX * vr}px ${FONT_FAMILY}`;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    const right = width - RIGHT_MARGIN_PX * hr;
    for (const { line, top } of this._tagPlacements(lines)) {
      // 中身は単一ソースから作る（表示名＝`priceTargetLabel`・価格＝`priceOnLine`）。
      const text = `${priceTargetLabel(targetOf(line))} ${priceOnLine(line.price, this._digits)}`;
      const w = textWidth(ctx, text, FONT_PX * vr) + TAG_PAD_X * 2 * hr;
      const h = TAG_H * vr;
      const y = top * vr;
      ctx.fillStyle = line.color;          // 塗り＝線と同色（どの線のタグかが色で分かる）
      // 塗りだけ半透明（残件 2・裁定「右端のままで半透明にする」）。不透明度は決め打ちでなく
      //   _refreshTagAlpha が「AA 4.5 を割らない最も透ける値」を下地の現実の色から導出した値。
      //   抜き文字まで透かすと合成色が文字色へ寄って自分のコントラストを壊すため、文字は不透明。
      ctx.globalAlpha = this._tagAlpha;
      ctx.fillRect(right - w, y, w, h);
      ctx.globalAlpha = 1;
      ctx.fillStyle = this._tagTextColor;  // 抜き文字＝地の色
      ctx.fillText(text, right - TAG_PAD_X * hr, y + h / 2);
    }
    ctx.restore();
  }

  // タグの縦位置（媒体座標の上端）。**重なりを 0 にする**割り当て。
  //
  // 規則: y の昇順に見て、既定は「線のすぐ上」に置き、直前に置いたタグの下端に食い込む場合だけ
  //   その下端まで**押し下げる**。上端は 0 でクランプする（版面の外に出さない）。
  //
  // なぜ上下振り分け（参照実装 `up`）ではなくこの方式か:
  //   振り分けは面が 2 つしかないため、3 本以上が近接すると必ず重なる（前版の既知の限界）。
  //   建値は K 本まで増える（実測: 分割本数 K は 1..N の入力）ので、3 本以上の近接は
  //   例外ではなく通常の使い方である。押し下げ方式は**本数によらず重なりが 0**になり、
  //   さらに「昇順に見て下へしか動かさない」ため**タグの並びが線の並びと必ず一致する**
  //   （タグ同士が入れ替わらない＝どのタグがどの線かの対応が崩れない）。
  //
  // **限界（明記）**: 3 本以上が密集すると、下側のタグは自分の線から離れていく
  //   （離れる量は密集した本数に比例する）。線そのものは正しい位置に引かれており、
  //   タグの並び順も線の並び順と一致するので対応は追えるが、**線とタグが 1 対 1 で
  //   隣り合うことは保証しない**。この限界を消すには吹き出しの引き出し線が要るが、
  //   それは裁定の範囲外（「小さなタグ」）なので実装しない。
  _tagPlacements(lines) {
    const sorted = [...lines].sort((a, b) => a.y - b.y);
    const out = [];
    let limit = 0;   // ここより上には置けない（直前のタグの下端 + すき間・初期値は版面の上端）
    for (const line of sorted) {
      const top = Math.max(line.y - TAG_H - TAG_GAP, limit);
      out.push({ line, top });
      limit = top + TAG_H + TAG_GAP;
    }
    return out;
  }

  // 描く線の一覧（価格・種別・色）。未指定（null/非有限）の水準は線を作らない。
  _lineSpecs() {
    const levels = this._levels;
    const specs = [];
    const entries = Array.isArray(levels.entryPrices) ? levels.entryPrices : [];
    entries.forEach((price, index) => {
      if (Number.isFinite(price)) {
        specs.push({ kind: 'entry', index, price, color: this._entryColor });
      }
    });
    if (Number.isFinite(levels.stopPrice)) {
      specs.push({ kind: 'stop', index: null, price: levels.stopPrice, color: this._stopColor });
    }
    if (Number.isFinite(levels.takePrice)) {
      specs.push({ kind: 'take', index: null, price: levels.takePrice, color: this._takeColor });
    }
    if (Number.isFinite(levels.losscutPrice)) {
      specs.push({
        kind: 'losscut', index: null, price: levels.losscutPrice, color: this._losscutColor,
      });
    }
    return specs;
  }

  // 線を引く横幅は **描画スコープ**から取る（`chart.timeScale().width()` を使わない）。
  //   理由 1: `timeScale` は `upstream_isolation_declaration.test.js` が施行する隔離対象 API で、
  //     本 primitive は宣言された隔離単位に含まれない（実測で Red になった）。幅を得るためだけに
  //     隔離宣言を広げるより、upstream に触らない経路へ寄せるほうが隔離が保てる。
  //   理由 2: 描画は bitmap 座標系で行うため、幅も同じ座標系の値を使うほうが整合する。
  //   幅が取れないときは 0（線を引かない）＝例外を投げない。
  //
  // **HiDPI の座標系（ISSUE-435 で確定・従来は未検証事項として保留していた）**
  //   既存実装には 2 つの流儀が同居している:
  //   (a) `tickvol_bands_primitive.js:116-117` / `replay_boundary_dim.js:91` は media 座標へ
  //       `scope.horizontalPixelRatio` を掛けてから描く
  //   (b) `market_profile_primitive.js:531,542` は `priceToCoordinate` の値（media 座標）を
  //       そのまま使い、幅だけ `scope.bitmapSize.width` を使う（`pair_lines_primitive.js` も無変換）
  //
  //   **(a) が正しい**。根拠は推測ではなく vendor の実装そのもの（実測 2026-08-21）:
  //     `web/vendor/lightweight-charts.js` の `useBitmapCoordinateSpace` は
  //     `this._context.save(); this._context.setTransform(1,0,0,1,0,0);` してから callback を呼ぶ
  //     ＝**変換を単位行列へ戻した装置ピクセル空間**で描かせる（canvas の実体は bitmapSize、
  //     `_horizontalPixelRatio = bitmapSize.width / mediaSize.width`）。lwc 自身の renderer も
  //     この空間で `horizontalPixelRatio` / `verticalPixelRatio` を掛けて描いている。
  //     一方 `priceToCoordinate` が返すのは media（CSS）座標なので、掛けずに描くと dpr>1 で
  //     **y が dpr 分の 1 の位置**に出る。(b) の 2 ファイルで見えにくかったのは、幅方向の
  //     取り違え（bitmapSize.width をそのまま使う）が「横に長すぎる」だけで見えないため。
  //
  //   したがって本 primitive は線もラベルも media 座標へ倍率を掛けて描く。掴み判定の y 表
  //   （`_handleYs`）は **media のまま**にする: drag が比べる相手はポインタ座標（media）であり、
  //   ここを装置ピクセルにすると dpr>1 で掴める位置と描画位置が割れる。
  //   dpr=1 では倍率が 1 なので、従来の描画命令と 1 px も変わらない（`TC-PL09` が固定）。
  //   dpr>1 の実機での見え方（線がローソクの価格と一致するか）は実 UI 検証で確認すること
  //   ＝**本変更は dpr>1 でのみ挙動が変わる**（従来はずれていたはずの側）。
  _extentWidth(scope) {
    const width = scope && (
      (scope.bitmapSize && scope.bitmapSize.width)
      ?? (scope.mediaSize && scope.mediaSize.width)
    );
    return Number.isFinite(width) ? width : 0;
  }
}

// 媒体座標 → 装置ピクセルの倍率。取れないときは 1（＝従来と同一・例外を投げない）。
function scale(ratio) {
  return Number.isFinite(ratio) && ratio > 0 ? ratio : 1;
}

// 文字の幅（装置ピクセル）。実 canvas は必ず `measureText` を持つ。持たない描画文脈
//   （最小 fake）では等幅の概算へ落とす＝幅が測れないだけで例外にしない。
function textWidth(ctx, text, fontPx) {
  return typeof ctx.measureText === 'function'
    ? ctx.measureText(text).width
    : text.length * fontPx * 0.6;
}

// 線の種別 → 表示名を引くための対象名。`entry:${i}` の作り方をここ 1 か所に置く
//   （モーダル・解除項目と同じ鍵の形。散らすと 0 始まり／1 始まりが割れる）。
function targetOf(line) {
  return line.kind === 'entry' ? `entry:${line.index}` : line.kind;
}
