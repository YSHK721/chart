// SeriesDrawer（adapter/front/series_drawer.js）— 系列生成・スタイルの協働クラス
//   （SOLID 是正 🔴-2: chart_renderer.js から 1:1 抽出）。
//
// ChartRenderer（ファサード）の内部協働子。共有状態（_instances スロット Map / _chart / _lwc /
//   _mainSeries / _mainStretchSet / _overlayReadouts）は ChartRenderer が所有し続け、本クラスは
//   コンストラクタで注入された host 参照経由で読み書きする（協働子間の直接依存は作らない＝
//   ISSUE-150 の pane スケール復元は host のラッパ経由で ScaleController に届く）。公開面
//   （ChartRenderer の public メソッド・export）は不変で、実体だけが本ファイルへ移動した。

import { seriesKind } from '../../domain/series_kind.js';

// lineStyle 文字列 → lightweight-charts LineStyle 整数（v4/v5 共通: Solid=0 / Dotted=1 / Dashed=2）。
const LINE_STYLE_INT = Object.freeze({ solid: 0, dotted: 1, dashed: 2 });

// ドット（サークル）表示の明示半径（px）。スタイルタブで dots へ切替時に付与し視認性を確保する
//   （lwc 既定 pointMarkersRadius は lineWidth/2+2＝細い）。adapter 既定 emit（_POINT_RADIUS=3.5）と一致。
const _POINT_MARKERS_RADIUS = 3.5;

function toLineStyleInt(style) {
  return LINE_STYLE_INT[style] ?? LINE_STYLE_INT.solid;
}

// メイン（ローソク）pane と オシレータ pane の高さ相対比。ローソクを大きく見せる初期値。
//   ユーザーは pane separator のドラッグ（機能④）で後から自由に調整できる。
const MAIN_PANE_STRETCH = 3;
const INDICATOR_PANE_STRETCH = 1;

export const WATERMARK_COLOR = 'rgba(209, 212, 220, 0.9)';

// σ 水準線のカラースキーム（histogram の level_colors と同義: 中心からの距離で 緑→赤）。
// 端点は common/level_colors.py の _CALM/_HOT（#2e7d32 / #d32f2f）に一致させる。
const SCHEME_CALM = [46, 125, 50]; // 緑（中心＝穏やか）
const SCHEME_HOT = [211, 47, 47]; // 赤（両極端＝過熱）
// 明度係数（背景 #131722 に馴染ませる。小さいほど暗い。0..1）。灰一色より色で識別でき、かつ控えめ。
const LEVEL_LINE_DIM = 0.55;

function lerp(a, b, t) {
  return a + (b - a) * t;
}

// 中心からの距離比 t∈[0,1] を 緑→赤 へ補間し dim で減光した rgb 文字列にする。
function schemeColor(t, dim) {
  const r = Math.round(lerp(SCHEME_CALM[0], SCHEME_HOT[0], t) * dim);
  const g = Math.round(lerp(SCHEME_CALM[1], SCHEME_HOT[1], t) * dim);
  const b = Math.round(lerp(SCHEME_CALM[2], SCHEME_HOT[2], t) * dim);
  return `rgb(${r}, ${g}, ${b})`;
}

// 系列データ末尾点の value を取り出す（読み取り欄の hover 解除時 fallback 用）。空なら null。
export function lastPointValue(data) {
  const arr = data ?? [];
  if (arr.length === 0) {
    return null;
  }
  const last = arr[arr.length - 1];
  return (last && last.value !== undefined) ? last.value : null;
}

export class SeriesDrawer {
  // host: ChartRenderer インスタンス（共有状態 _instances/_chart/_lwc/_mainSeries 等の所有者）。
  constructor(host) {
    this._h = host;
  }

  _slot(instanceId) {
    let slot = this._h._instances.get(instanceId);
    if (!slot) {
      slot = {
        lines: new Map(), priceLines: [], hlinePayloads: null, visible: true,
        // scaleHost: 当該 instance の line/histogram 系列の先頭（水準線の載せ先・pane の価格軸基準）。
        // priceLineHost: 水準線（createPriceLine）を載せた系列（pane=scaleHost / overlay=mainSeries）。
        // pane/watermark/paneName: pane 指標のみ（機能①②）。overlay 指標は pane 0 のため null。
        scaleHost: null, priceLineHost: null, pane: null, watermark: null, paneName: null,
        // styleMeta（ISSUE-109）: 系列キー -> { name, kind, color, width, style, visible }。
        //   生成時スタイルの記録＋applySeriesStyle の上書き結果を保持し、スタイルタブの
        //   初期表示（実描画値）と instance 単位 setVisible との可視性合成に使う。
        styleMeta: new Map(),
        // 系列キー -> 末尾点の値（ISSUE-276・ペイン別凡例のクロスヘア無し表示値）。
        lastValues: new Map(),
        // seriesData（案A・btlm_trail_marod）: 系列キー -> 直近 setData 済みポイント配列。
        //   bar_editable ゲート済み系列のみ保持し（MAROD 限定・メモリ極小）、line ⇄ histogram の
        //   系列スワップ時に新系列へ再設定する（旧系列除去後にデータを失わないため）。
        seriesData: new Map(),
      };
      this._h._instances.set(instanceId, slot);
    }
    return slot;
  }

  // level_dash（同値 4 値の Candlestick）の色オプション写像。CandlestickSeries は `color` を
  //   持たず up/down/border/wick の 6 経路で着色するため、単色をすべてへ複製する。
  //   open==close の同事は上下判定が処理系依存なので、どちらに転んでも同色になるようにする。
  //   **生成時（_renderSeries）と変更時（applySeriesStyle）の唯一の写像点**（乖離すると
  //   「スタイルで色を変えても反映されない」不具合になる・ISSUE-226）。
  _levelDashColors(color) {
    return {
      upColor: color, downColor: color,
      borderUpColor: color, borderDownColor: color,
      wickUpColor: color, wickDownColor: color,
    };
  }

  // pane 指標なら専用 pane を生成し指標名ウォーターマーク（機能①②）を立てる。overlay は null（pane 0）。
  _ensurePane(slot, opts) {
    if (!opts.pane) {
      return null;
    }
    if (slot.pane) {
      return slot.pane;
    }
    // 初回 pane 追加時にメイン（ローソク）pane を大きめへ（以後ユーザーのドラッグを尊重し再設定しない）。
    if (!this._h._mainStretchSet) {
      const panes = this._h._chart.panes ? this._h._chart.panes() : [];
      if (panes[0] && typeof panes[0].setStretchFactor === 'function') {
        panes[0].setStretchFactor(MAIN_PANE_STRETCH);
      }
      this._h._mainStretchSet = true;
    }
    // v5 は空 pane を既定で自動削除する。系列の再計算（remove→redraw）で一時的に空になった
    // 瞬間に pane が消えて index がずれ、直後の removePane が誤 pane を対象化／例外となり、
    // 再描画前に処理が中断して指標が消える。preserveEmptyPane=true で pane の寿命を removePane
    // のみの単一権威にする（ISSUE: period 変更で Volatility 等が消える不具合の根治）。
    const pane = this._h._chart.addPane(true);
    if (pane && typeof pane.setPreserveEmptyPane === 'function') {
      pane.setPreserveEmptyPane(true);
    }
    if (pane && typeof pane.setStretchFactor === 'function') {
      pane.setStretchFactor(INDICATOR_PANE_STRETCH);
    }
    slot.pane = pane;
    slot.paneName = opts.name ?? '';
    // ISSUE-276: ペイン左上のテキストウォーターマーク（指標名＋系列値）は撤去した。
    //   同じ情報をペイン別凡例の行が持ち、凡例 DOM が canvas 上に載るため互いに重なって
    //   判読不能になっていた（実測 2026-08-06）。表示系統は凡例 1 つに統合する。
    return pane;
  }

  // line / histogram を共通生成する（upstream API 名 addSeries は本所のみ）。
  _renderSeries(instanceId, payloads, kind, opts = {}) {
    const slot = this._slot(instanceId);
    const pane = this._ensurePane(slot, opts);
    // seriesType → lightweight-charts の系列定義。台帳（series_kind）の宣言のみで決まる。
    const seriesType = seriesKind(kind).seriesType;
    const definition = seriesType === 'histogram'
      ? this._h._lwc.HistogramSeries
      : seriesType === 'level_dash'
        ? this._h._lwc.CandlestickSeries
        : this._h._lwc.LineSeries;
    for (const p of payloads ?? []) {
      // 価格軸（画面右端）のラベルは系列名ではなく現在値（数値・系列色チップ）を表示する
      //   （ユーザー指示 2026-07-23。旧: title=系列名＋lastValueVisible=false＝名前チップ）。
      const options = {
        color: p.color,
        priceLineVisible: false,
        lastValueVisible: true,
      };
      if (seriesKind(kind).appliesLineStyle) {
        options.lineWidth = p.width;
        options.lineStyle = toLineStyleInt(p.style);
      }
      // btlm_trail 表示層: ドット/ライン切替ヒント（point_markers/line_visible）を
      //   lightweight-charts v5 の LineSeries オプションへ写像する。ヒント未付与の payload
      //   （既存指標）はキーを設定しない＝従来挙動を保つ（後方互換）。
      if (p.point_markers !== undefined) {
        options.pointMarkersVisible = !!p.point_markers;
      }
      if (p.line_visible !== undefined) {
        options.lineVisible = !!p.line_visible;
      }
      if (p.point_markers_radius !== undefined) {
        options.pointMarkersRadius = p.point_markers_radius;
      }
      // 読取欄専用系列（β・被覆率・σ 等の小値系列）はチャート/価格軸に一切の視覚要素を出さない
      //   （値供給は crosshair 読取欄のみ）。以下を無効化する:
      //   - autoscaleInfoProvider→{priceRange:null}: 価格軸オートスケールへ寄与しない（bundle 実装
      //     で確認済: 返値 {priceRange:null} は範囲寄与なし。null 返しは既定＝系列データを含むため誤り）。
      //   - title='': 価格軸の名前ラベルは series.title 由来（lastValueVisible とは独立に描画される）。
      //     手動スケール/ズームで軸レンジが系列値域（0〜数千）を含むと露出するため空にして抑止する。
      //   - lastValueVisible/priceLineVisible=false: 最終値ラベル・プライスライン（通常系列は
      //     lastValueVisible=true＝数値チップ表示のため、読取専用はここで必ず無効化する）。
      //   - crosshairMarkerVisible=false: ホバー時のクロスヘアマーカー（点）も出さない。
      if (p.readout_only) {
        options.autoscaleInfoProvider = () => ({ priceRange: null });
        options.title = '';
        options.lastValueVisible = false;
        options.priceLineVisible = false;
        options.crosshairMarkerVisible = false;
      }
      // pane 指標は専用 pane（IPaneApi.addSeries）、overlay 指標は pane 0（IChartApi.addSeries）。
      // level_dash: 同値 4 値の同事（doji）＝実体が潰れて水平線 1 本になり、幅は
      //   ローソク足と一致する。ヒゲは消す。色は 4 経路すべてへ同じ値を入れる
      //   （open==close は上下判定が処理系依存のため、どちらに転んでも同色にする）。
      if (seriesType === 'level_dash') {
        options.wickVisible = false;
        Object.assign(options, this._levelDashColors(p.color));
      }
      const series = pane
        ? pane.addSeries(definition, options)
        : this._h._chart.addSeries(definition, options);
      // payload 契約は line と同一（{time, value}）。level_dash のみ表示層で 4 値へ展開する
      //   （back の payload 形状を増やさないための写像点＝ここが唯一）。
      const data = p.data ?? [];
      series.setData(seriesType === 'level_dash'
        ? data.map((d) => ({ time: d.time, open: d.value, high: d.value, low: d.value, close: d.value }))
        : data);
      const key = `${instanceId}::${p.name}`;
      slot.lines.set(key, series);
      const metaEntry = {
        name: p.name, kind, color: p.color ?? null,
        width: p.width ?? null, style: p.style ?? null, visible: true,
        // heat（ISSUE-112）: histogram でバー別着色（data[].color＝値に応じたヒート配色）を持つか。
        //   heat=true の系列はユーザー色上書きの対象外（ヒート絶対優先・ユーザー裁定）。
        heat: seriesKind(kind).supportsHeat && (p.data ?? []).some((pt) => pt && pt.color != null),
        // display（案A・btlm_trail）: 系列表示（ドット/ライン）の現在値。payload の描画ヒント
        //   （point_markers/line_visible）から導出。ヒント無し系列は null（＝この属性を持たない）。
        display: p.point_markers ? 'dots' : (p.line_visible ? 'line' : null),
      };
      // 案A（btlm_trail_marod）: barStyleEditable ゲート系列（controller が bar_editable=true を注入）は
      //   line ⇄ histogram スワップの対象。styleMeta へ barEditable=true を刻み（applySeriesStyle の
      //   二重ゲート源）、保持データを seriesData へ退避する（旧系列除去後の再設定用）。非ゲート系列は
      //   barEditable キーを持たず seriesData にも載せない（native histogram 他指標へ非波及＝挙動不変）。
      if (p.bar_editable === true) {
        metaEntry.barEditable = true;
        slot.seriesData.set(key, p.data ?? []);
      }
      slot.styleMeta.set(key, metaEntry);
      // ISSUE-276: 末尾点の値。ペイン別凡例が「クロスヘアが無いときの表示値」に使う。従来は
      //   overlay 系列だけが _overlayReadouts に持っており、pane 指標はクロスヘアを乗せないと
      //   値が出せなかった。styleMeta とは別の Map に持つ（styleMeta はプロパティダイアログへ
      //   渡るスタイル契約であり、実行時の値を混ぜない）。
      slot.lastValues.set(key, lastPointValue(p.data));
      if (!slot.scaleHost) {
        slot.scaleHost = series;
      }
      // overlay（pane 0 重ね描き）の line 系列を読み取り欄の overlay 行に載せる。
      //   color/name と末尾点 value（hover 解除時の fallback）を保持する。
      //
      // readout_only の系列は pane 指標でも載せる: このヒントは「描画せず読取欄だけに出す」
      //   という意味であり（back の描画ヒント契約・fake_chart の _DISPLAY_HINTS）、pane だから
      //   除外すると線も出ず読取欄にも出ない＝どこにも現れない死荷重になる。対象は明示的に
      //   readout_only を付けた系列だけなので、既存指標の読取欄行は 1 行も増えない。
      if ((!pane && seriesKind(kind).overlayReadout) || p.readout_only === true) {
        this._h._overlayReadouts.set(key, {
          series, color: p.color, name: p.name, lastValue: lastPointValue(p.data),
          visible: true,
        });
      }
    }
    // ISSUE-150: keepPane redraw で退避した pane 価格軸の手動レンジを、系列再追加後に復元する。
    //   退避が無い（自動スケール中・初回描画）は no-op。
    if (pane) {
      this._h._restorePaneScaleRange(slot);
    }
  }

  _createPriceLines(slot, hlines) {
    const host = slot.scaleHost ?? this._h._mainSeries;
    slot.priceLineHost = host;
    // pane 指標（オシレータ）の σ 水準線には histogram と同じカラースキーム（中心からの距離で
    // 緑→赤）を減光して適用し、灰一色で背景に埋もれる問題を改善する。overlay バンド
    // （price_range_power / hl_band 等）は bull/bear 等の意味付き色を持つため backend 色を維持。
    const lines = hlines ?? [];
    const useScheme = !!slot.pane && lines.length > 0;
    let center = 0;
    let maxDist = 0;
    if (useScheme) {
      const prices = lines.map((h) => h.price);
      center = (Math.max(...prices) + Math.min(...prices)) / 2;
      maxDist = Math.max(...prices.map((p) => Math.abs(p - center)));
    }
    for (const h of lines) {
      const color = useScheme
        ? schemeColor(maxDist > 0 ? Math.abs(h.price - center) / maxDist : 0, LEVEL_LINE_DIM)
        : h.color;
      const pl = host.createPriceLine({
        price: h.price,
        color,
        lineWidth: h.width,
        lineStyle: toLineStyleInt(h.style),
        title: h.text,
        axisLabelVisible: h.axis_label_visible ?? false,
      });
      slot.priceLines.push(pl);
    }
  }

  // UC-04 表示/非表示。line/histogram は applyOptions({visible})、priceLine は除去/再生成。
  //   ISSUE-109: 系列単位の可視性（styleMeta.visible）と AND 合成する（インスタンス eye ON へ
  //   戻しても、スタイル設定で個別非表示にした系列は非表示のまま）。
  setVisible(instanceId, visible) {
    const slot = this._h._instances.get(instanceId);
    if (!slot) {
      return;
    }
    slot.visible = visible;
    for (const [key, series] of slot.lines) {
      const seriesVisible = visible && (slot.styleMeta.get(key)?.visible ?? true);
      series.applyOptions({ visible: seriesVisible });
      // 読み取り欄の overlay 行も表示状態へ追従させる（非表示は欄から除外）。
      const meta = this._h._overlayReadouts.get(key);
      if (meta) {
        meta.visible = seriesVisible;
      }
    }
    if (slot.hlinePayloads !== null) {
      if (visible && slot.priceLines.length === 0) {
        this._createPriceLines(slot, slot.hlinePayloads);
      } else if (!visible && slot.priceLines.length > 0) {
        this._removePriceLines(slot);
      }
    }
  }

  // ISSUE-109: 系列単位のスタイル上書き（色/線幅/線種/可視性）。patch は差分のみ指定可。
  //   lwc series.applyOptions で即時反映（再計算不要・仕様 §6.1）。styleMeta と overlay 読み取り欄の
  //   色/可視性も同期する。未知系列は no-op（false）。可視性はインスタンス可視と AND 合成。
  applySeriesStyle(instanceId, seriesName, patch = {}) {
    const slot = this._h._instances.get(instanceId);
    if (!slot) {
      return false;
    }
    const key = `${instanceId}::${seriesName}`;
    const series = slot.lines.get(key);
    const meta = slot.styleMeta.get(key);
    if (!series || !meta) {
      return false;
    }
    // ISSUE-112（ユーザー裁定）: バー別ヒート配色（heat）の histogram は色 patch を無視する
    //   （ヒート表示が絶対優先・データ全塗り替えでヒートを潰す ISSUE-111 の機構は撤去）。
    //   heat 以外の histogram は series options.color が素で効く（バー別色が無いため上書き不要）。
    if (patch.color != null && !(seriesKind(meta.kind).supportsHeat && meta.heat)) {
      meta.color = patch.color;
    }
    if (patch.width != null) {
      meta.width = patch.width;
    }
    if (patch.style != null) {
      meta.style = patch.style;
    }
    if (patch.visible !== undefined && patch.visible !== null) {
      meta.visible = !!patch.visible;
    }
    // 案A（btlm_trail_marod・二重ゲート）: barEditable ゲート済み系列のみ line ⇄ histogram の系列種別を
    //   スワップする。meta.barEditable===true 以外（native histogram 他指標・全 line 他指標）は本分岐に
    //   一切入らず現行 applyOptions 経路のみ（棒→線の誤変換を構造的に遮断）。color/width/style/visible は
    //   上で meta へ反映済みのため、_swapSeriesType が新系列の生成オプションへそのまま引き継ぐ。
    if (meta.barEditable === true) {
      // 現在の描画種別は能力台帳（seriesType）で判定する（raw kind 文字列比較を持ち込まない・ISSUE-134）。
      const isHistogram = seriesKind(meta.kind).seriesType === 'histogram';
      const toBar = patch.display === 'bar' && !isHistogram;
      const toLine = isHistogram
        && (patch.display === 'line' || patch.display === 'dots');
      if (toBar || toLine) {
        if (toLine) {
          // 棒→線: 統合 select は {display:'line', style} か {display:'dots'} を渡す。線種は meta へ
          //   反映済み（上の patch.style 分岐）。display を確定してから line 系列を再生成する。
          meta.display = patch.display;
        }
        this._swapSeriesType(slot, key, meta, toBar ? 'histogram' : 'line');
        return true;
      }
    }
    const options = { color: meta.color, visible: slot.visible && meta.visible };
    // level_dash は CandlestickSeries であり `color` オプションを持たない。生成時と同じ
    //   写像を通さないと色変更が黙って無視される（ISSUE-226）。
    if (seriesKind(meta.kind).seriesType === 'level_dash' && meta.color != null) {
      Object.assign(options, this._levelDashColors(meta.color));
    }
    if (seriesKind(meta.kind).appliesLineStyle) {
      if (meta.width != null) {
        options.lineWidth = meta.width;
      }
      if (meta.style != null) {
        options.lineStyle = toLineStyleInt(meta.style);
      }
    }
    // display（案A・btlm_trail）: 系列表示（dots/line）を lightweight-charts の
    //   pointMarkersVisible/lineVisible へ写像する。patch.display 未指定なら一切触らない（非破壊・
    //   他指標の系列は display を持たないため影響なし）。dots は明示半径で視認性を確保する。
    if (patch.display === 'dots' || patch.display === 'line') {
      meta.display = patch.display;
      const dots = patch.display === 'dots';
      options.pointMarkersVisible = dots;
      options.lineVisible = !dots;
      if (dots) {
        options.pointMarkersRadius = _POINT_MARKERS_RADIUS;
      }
    }
    series.applyOptions(options);
    const ro = this._h._overlayReadouts.get(key);
    if (ro) {
      ro.color = meta.color;
      ro.visible = slot.visible && meta.visible;
    }
    return true;
  }

  // 案A（btlm_trail_marod）: 系列の描画種別を line ⇄ histogram へ差し替える（同一キー維持・保持データ
  //   再設定）。既存の remove()/価格線機構を再構成した最小再生成。barEditable ゲート済み系列のみが本
  //   経路へ入る（applySeriesStyle の二重ゲート）。順序規律は remove() と同一（価格線を系列除去より先に外す）。
  _swapSeriesType(slot, key, meta, toKind) {
    const oldSeries = slot.lines.get(key);
    if (!oldSeries) {
      return false;
    }
    // 1. 0% 基準線（priceLine）を旧 host（当の系列）から先に外す（pane 配置では host が当の系列のため）。
    const hadPriceLines = slot.priceLines.length > 0;
    this._removePriceLines(slot);
    // 2. 旧系列を除去（pane 系列も chart.removeSeries で除去＝remove() と同一経路）。
    this._h._chart.removeSeries(oldSeries);
    // 3. 遷移先種別の系列定義（histogram/line）を選ぶ。
    const toHistogram = seriesKind(toKind).seriesType === 'histogram';
    const definition = toHistogram ? this._h._lwc.HistogramSeries : this._h._lwc.LineSeries;
    // 4. 生成オプション。histogram は 0% 中心（base:0・lineWidth/lineStyle/pointMarkers は出さない）。
    //    line は幅/線種と display 写像（pointMarkers/lineVisible）を meta から復元する。色は両者で活かす。
    // 価格軸ラベルは現在値（数値）表示（_renderSeries と同一仕様・ユーザー指示 2026-07-23）。
    const options = {
      color: meta.color,
      priceLineVisible: false,
      lastValueVisible: true,
      visible: slot.visible && meta.visible,
    };
    if (toHistogram) {
      options.base = 0;
    } else {
      options.lineWidth = meta.width ?? 1;
      options.lineStyle = toLineStyleInt(meta.style);
      const dots = meta.display === 'dots';
      options.pointMarkersVisible = dots;
      options.lineVisible = !dots;
      if (dots) {
        options.pointMarkersRadius = _POINT_MARKERS_RADIUS;
      }
    }
    // 5. 新系列を生成し保持データ（seriesData）を再設定する（pane 指標は pane 経由・overlay は chart）。
    const newSeries = slot.pane
      ? slot.pane.addSeries(definition, options)
      : this._h._chart.addSeries(definition, options);
    newSeries.setData(slot.seriesData.get(key) ?? []);
    // 6. 同一キーで差し替え（クロスヘア走査・setData/updateSeriesTail が自然追従する）。
    slot.lines.set(key, newSeries);
    // 7. scaleHost が旧系列だったら新系列へ張り替える（pane 価格軸の基準）。
    if (slot.scaleHost === oldSeries) {
      slot.scaleHost = newSeries;
    }
    // 8. meta 更新: kind と display（histogram は 'bar'・往復整合）。
    meta.kind = toKind;
    meta.display = toHistogram ? 'bar' : meta.display;
    // overlay 読取欄に載っている場合は series 参照を張り替える（MAROD は pane のため通常無い）。
    const ro = this._h._overlayReadouts.get(key);
    if (ro) {
      ro.series = newSeries;
    }
    // 9. 0% 基準線を新 host（新系列）へ再生成する（元々あった場合のみ実体を持つ＝可視性不変）。
    if (hadPriceLines) {
      this._createPriceLines(slot, slot.hlinePayloads);
    }
    return true;
  }

  _removePriceLines(slot) {
    const host = slot.priceLineHost ?? this._h._mainSeries;
    for (const pl of slot.priceLines) {
      host.removePriceLine(pl);
    }
    slot.priceLines = [];
  }
}
