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

// 掴めない線の種別（読み取り専用）。ロスカットは口座状態から導出される結果であって入力ではない。
const READ_ONLY_KINDS = new Set(['losscut']);

// 線種ごとの破線パターン（[] は実線）。ロスカットは「入力ではない」ことを見た目でも区別する。
const DASH = Object.freeze({ entry: [], stop: [], take: [], losscut: [4, 4] });

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
      for (const line of lines) {
        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = line.color;
        ctx.lineWidth = 1;
        if (typeof ctx.setLineDash === 'function') {
          ctx.setLineDash(DASH[line.kind] || []);
        }
        ctx.moveTo(0, line.y);
        ctx.lineTo(width, line.y);
        ctx.stroke();
        ctx.restore();
      }
    });
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
  // **未検証事項（HiDPI・実 UI 検証 NFR-09 で確定させる）**: 既存実装に 2 つの流儀が同居している。
  //   (a) `tickvol_bands_primitive.js:116-117` / `replay_boundary_dim.js:91` は media 座標へ
  //       `scope.horizontalPixelRatio` を掛けてから描く
  //   (b) `market_profile_primitive.js:531,542` は `priceToCoordinate` の値（media 座標）を
  //       そのまま使い、幅だけ `scope.bitmapSize.width` を使う（`pair_lines_primitive.js` も無変換）
  //   本 primitive は (b) に合わせている（実 UI で目視確認され続けている側の流儀）。
  //   横一本線は x が足りていれば足りるので（bitmapSize.width ≧ mediaSize.width）幅は安全側だが、
  //   **y の位置が dpr>1 でずれないか**はソースからは決められない。dpr=2 の実機で
  //   線がローソクの価格と一致することを確認すること。ずれた場合の是正は
  //   (a)(b) どちらが正しいかの確定を伴うため、本 primitive 単独では判断しない。
  _extentWidth(scope) {
    const width = scope && (
      (scope.bitmapSize && scope.bitmapSize.width)
      ?? (scope.mediaSize && scope.mediaSize.width)
    );
    return Number.isFinite(width) ? width : 0;
  }
}
