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
// lwc ライフサイクル（attached/detached/paneViews）は pair_primitive_base.js にも同型の記述がある。
//   共通基底へ括り出さなかったのは、pair_primitive_base が `_pairs` / `_highlight` /
//   `setPairs` / `setHighlight` という**ペア固有の状態**と一体化しており、そのまま継承すると
//   本 primitive に意味の無い公開面が生えるため（ISP/LSP）。ライフサイクルだけの基底を新設する
//   案は、既存の共有モジュール（売買マーカーが使用中）の変更＝本スライスの範囲外であり、
//   承認事項として別途提案する。ここで写しているのは lwc が要求する定型（業務規則ではない）。
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
// ラベルの**表示名と価格書式**は単一ソースから取る（ISSUE-435）。ここへ書き写すと、
//   モーダルの欄・アーム中バー・右クリックの解除項目と同じ表が 4 つ目に増え、
//   ゴーストと線で価格の書式が割れる（ISSUE-368 で実際に起きた症状と同型）。
import { priceOnLine, priceTargetLabel } from './price_format.js';

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

export class PriceLevelLinesPrimitive {
  constructor() {
    this._levels = null;
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
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
    this._paneView = { renderer: () => ({ draw: (target) => this.draw(target) }) };
  }

  // ---- lwc ISeriesPrimitive ライフサイクル ----

  attached({ chart, series, requestUpdate }) {
    this._chart = chart;
    this._series = series;
    this._requestUpdate = requestUpdate;
  }

  detached() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
    this._handleYs = [];
  }

  paneViews() {
    return [this._paneView];
  }

  // ---- 状態 ----

  /**
   * 価格の表示桁を受ける（ISSUE-435）。`position_sizing_dialog.setSymbolSpec` と同じ名前・同じ
   * 規約にする（新しい配り方を作らない）。解決点は共有配線の 1 か所で、ここは配られるだけ。
   * @param {{digits:number}|null|undefined} spec 解決できないときは null（＝整数表示のまま）。
   */
  setSymbolSpec(spec) {
    this._digits = spec ? spec.digits : undefined;
    if (typeof this._requestUpdate === 'function') {
      this._requestUpdate();
    }
  }

  // 水準を差し替えて再描画を要求する（attach 前は要求だけ no-op）。
  //   levels: { direction, entryPrices[], stopPrice, takePrice|null, losscutPrice|null }
  setLevels(levels) {
    this._levels = levels || null;
    if (typeof this._requestUpdate === 'function') {
      this._requestUpdate();
    }
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
    if (typeof this._requestUpdate === 'function') {
      this._requestUpdate();
    }
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

  draw(target) {
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
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const width = this._extentWidth(scope);
      // 媒体（CSS）座標 → 装置ピクセル。`priceToCoordinate` は媒体座標を返すのに対し、
      //   `useBitmapCoordinateSpace` は変換を単位行列へ戻す（vendor 実測: 下の注記）ため、
      //   dpr を掛けないと dpr>1 で線もラベルも半分の位置・半分の大きさになる。
      const hr = scale(scope.horizontalPixelRatio);
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
      this._drawTags(ctx, lines, { width, hr, vr });
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
      ctx.fillRect(right - w, y, w, h);
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
