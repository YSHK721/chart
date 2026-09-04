// CrosshairReadoutBuilder（adapter/front/crosshair_readout_builder.js）— 読み取りロールの協働クラス
// @upstream-isolation: crosshair_readout_builder.js
//   （ISSUE-479 Wave2 J-2c: chart_renderer.js から 1:1 抽出。ScaleController / CandleFeed /
//    SeriesDrawer / PaneGeometryController / ChromeColorController と同形＝生 host 参照を受け取る）。
//
// 担う関心は 1 つ:「いま指している所（クロスヘア位置・指定 x 座標）から**読める値**を、
//   プレーンなデータ構造として組み立てて渡す」。series 実体・lwc 型は外へ出さない（§2.2 隔離）。
//
// 状態の所有（ISSUE-181「状態も一緒に移す」）: 読み取り欄のコールバック（_onCrosshairReadout）・
//   tf-period ホバー座標ハンドラ（_onTfPeriodHover）・sessions の当日 MP（_sessionMP）は
//   **本クラスが所有する**。
//
// host に残る共有状態（本クラスは読むだけ）: _chart / _mainSeries / _instances / _lastBar と、
//   ローソク列（getCandles）・ペイン別凡例（paneLegendModel）・ペイン分類（_pricePaneIndex /
//   _slotPaneIndex）・凡例の発行（_emitPaneLegend）。協働子間の直接依存は作らず host 経由で辿る。

// time 昇順の点列から time が一致する点を返す（無ければ undefined）。ローソク・指標系列とも
//   lightweight-charts のデータ規約で time 昇順のため二分探索で引く（右クリック 1 回あたり
//   系列数ぶんの探索になるので線形走査にしない）。time は UTCTimestamp（数値）を前提とし、
//   business day 形式など数値でない時刻表現は「引けない」（undefined）＝黙って別の足を返さない。
export function pointAtTime(points, time) {
  if (!Array.isArray(points) || typeof time !== 'number') {
    return undefined;
  }
  let lo = 0;
  let hi = points.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const t = points[mid] ? points[mid].time : undefined;
    if (typeof t !== 'number') {
      return undefined;
    }
    if (t === time) {
      return points[mid];
    }
    if (t < time) {
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return undefined;
}

// 系列の指定 time の値（線・ヒストグラム＝value / ローソク＝close）。無ければ undefined。
//   series.data() は upstream API のため呼び出しは隔離単位の中に閉じる（隔離維持）。
export function pointValueAt(series, time) {
  if (!series || typeof series.data !== 'function') {
    return undefined;
  }
  const p = pointAtTime(series.data(), time);
  if (p === undefined || p === null) {
    return undefined;
  }
  return (typeof p === 'object') ? (p.value ?? p.close) : p;
}

export class CrosshairReadoutBuilder {
  // host: ChartRenderer インスタンス。opts.onCrosshairReadout: 読み取り DTO の配線先
  //   （省略時 no-op＝後方互換）。
  constructor(host, { onCrosshairReadout } = {}) {
    this._h = host;
    this._onCrosshairReadout = typeof onCrosshairReadout === 'function' ? onCrosshairReadout : () => {};
    // tf-period ホバー座標ハンドラ（setTfPeriodHoverHandler で配線・null で解除）。
    this._onTfPeriodHover = null;
    // sessions（日別プロファイル）の time→{poc,vah,val} Map（読み取り欄で当日 MP を出す）。null=非表示。
    this._sessionMP = null;
  }

  // クロスヘア移動でペイン別凡例（値）とクロスヘア価格読み取り欄（OHLC）を更新する。
  //   ISSUE-276: 旧「ペイン左上ウォーターマークへ指標名＋値を焼く」経路は撤去した。同じ情報を
  //   凡例行が持つため 2 系統になっており、凡例 DOM がウォーターマークの上に載って判読不能だった。
  _onCrosshairMove(param) {
    this._h._emitPaneLegend(param);
    // クロスヘア価格読み取り欄（左上オーバーレイ）への DTO 発火。
    this._emitReadout(param);
    // tf-period ホバー読取（依頼者指示 2026-07-13・a案ツールチップ）: カーソル位置の座標 DTO
    //   { x, y, time, price } を配線先（composition root）へ渡す。lwc 型は渡さない（隔離維持）。
    //   カーソルがチャート外（point 無し）は null＝ツールチップ hide。ハンドラ未設定は no-op。
    if (typeof this._onTfPeriodHover === 'function') {
      const pt = param && param.point;
      if (pt && param.time != null && typeof this._h._mainSeries.coordinateToPrice === 'function') {
        const price = this._h._mainSeries.coordinateToPrice(pt.y);
        this._onTfPeriodHover(price != null
          ? { x: pt.x, y: pt.y, time: Number(param.time), price: Number(price) }
          : null);
      } else {
        this._onTfPeriodHover(null);
      }
    }
  }

  // tf-period ホバー座標ハンドラを設定する（composition root が配線・null で解除）。
  setTfPeriodHoverHandler(fn) {
    this._onTfPeriodHover = typeof fn === 'function' ? fn : null;
  }

  // 読み取り DTO を構築してコールバックへ渡す。param=null（ライブ更新由来）は hover 解除扱い。
  _emitReadout(param) {
    this._onCrosshairReadout(this._buildReadoutDto(param));
  }

  // slot の各系列の表示値。**どの値を取るか**は picker（Strategy）が決め、本メソッドは
  //   「どの系列を出すか（可視の扱い）」と「名前・色をどう付けるか」だけを担う。
  //   系列単位で非表示（styleMeta.visible=false）のものは出さない（凡例と描画を一致させる）。
  //   picker: (series, key) => value|undefined。
  _slotValues(slot, pick) {
    const out = [];
    if (slot.visible === false) {
      return out;   // インスタンスごと非表示（eye OFF）＝値は出さない（行は残す＝再表示できる）。
    }
    for (const [key, series] of slot.lines) {
      const meta = slot.styleMeta ? slot.styleMeta.get(key) : null;
      if (meta && meta.visible === false) {
        continue;
      }
      out.push({ name: meta ? meta.name : key, value: pick(series, key), color: meta ? meta.color : undefined });
    }
    return out;
  }

  // 既定の値取り出し（凡例の規約）: クロスヘア位置に値があればそれ、無ければ保持した最新値。
  _crosshairValue(slot, series, key, seriesData) {
    const d = seriesData ? seriesData.get(series) : undefined;
    let value;
    if (d !== undefined && d !== null) {
      value = (typeof d === 'object') ? (d.value ?? d.close) : d;
    }
    if (value === undefined || value === null) {
      value = slot.lastValues ? slot.lastValues.get(key) : undefined;
    }
    return value;
  }

  // 読み取り DTO を構築する（プレーンなデータ構造・series 実体や lwc 型は含めない＝隔離維持）。
  //   { time, ohlc:{open,high,low,close}|null, overlays:[{name,value,color}] }。
  _buildReadoutDto(param) {
    const seriesData = (param && param.seriesData) || null;
    // main OHLC: seriesData に main があればそれ、無ければ（hover 解除）最新足 _lastBar へフォールバック。
    const mainData = seriesData ? seriesData.get(this._h._mainSeries) : undefined;
    const src = (mainData !== undefined && mainData !== null) ? mainData : this._h._lastBar;
    const ohlc = (src && src.open !== undefined)
      ? { open: src.open, high: src.high, low: src.low, close: src.close }
      : null;
    // ISSUE-276: overlay 各系列の値は**ペイン別凡例の行**が持つ（読み取り欄からは外す）。
    //   同じ値を 2 系統に出していたため、指標が増えるほど読み取り欄が伸びて凡例と重なっていた
    //   （実測: 指標 11 件で読み取り欄 229px＋凡例 295px）。読み取り欄は OHLC と時刻だけを担う。
    //   overlays は空配列で残す（View・既存呼出の形を壊さない）。
    const overlays = [];
    const time = (param && param.time !== undefined) ? param.time
      : (this._h._lastBar ? this._h._lastBar.time : undefined);
    // sessions: 当日 MP（POC/VAH/VAL）を time で引いて DTO に載せる（供給時のみ・sessions 表示中）。
    const sessionMP = (this._sessionMP && time != null) ? (this._sessionMP.get(time) || null) : null;
    return { time, ohlc, overlays, sessionMP };
  }

  /**
   * チャート要素の左上を原点とする x 座標が指す足の情報を返す（ユーザー指示 2026-08-09・右クリックコピー）。
   *
   * 返すのは情報ウィンド（クロスヘア読み取り欄＋ペイン別凡例）と**同じ材料**で、
   *   { time, ohlc:{open,high,low,close}|null, sessionMP:{poc,vah,val}|null,
   *     indicators: [{ instanceId, values: [{ name, value, color }] }] }
   * 座標→足の解決は upstream（timeScale().coordinateToTime）に触れる本ロールに閉じる。
   *
   * クロスヘア経路との違いは **値が無い足で最新値へ落ちない**ことだけ（凡例は「クロスヘアが
   * 無ければ最新値」という表示規約を持つが、足を名指しでコピーする場面でその足に無い値を
   * 最新値で埋めると、別の足の値を「その足の値」として配ってしまう）。
   *
   * @param {number} x  チャート要素の左上基準の x（px）。
   * @returns {object|null} 足が無い座標（データ範囲外・時間軸未確定）は null。
   */
  barInfoAt(x) {
    const time = this._timeAtCoordinate(x);
    if (time == null) {
      return null;
    }
    const candle = pointAtTime(this._h.getCandles(), time);
    const ohlc = (candle && candle.open !== undefined)
      ? { open: candle.open, high: candle.high, low: candle.low, close: candle.close }
      : null;
    const model = this._h.paneLegendModel(null, () => (series) => pointValueAt(series, time));
    const indicators = [];
    for (const g of model.groups) {
      for (const r of g.rows ?? []) {
        indicators.push({ instanceId: r.instanceId, values: r.values ?? [] });
      }
    }
    const sessionMP = this._sessionMP ? (this._sessionMP.get(time) || null) : null;
    return { time, ohlc, sessionMP, indicators };
  }

  /**
   * ISSUE-368 スライス 8-b: x 座標が指す足の**スナップ候補**をプレーンデータで列挙する。
   *
   * @param {number} x チャート要素の左上基準の x（px）。
   * @returns {Array<{kind:string,label:string,price:number}>|null}
   *   足が無い座標（データ範囲外・時間軸未確定）は null。
   */
  snapCandidatesAt(x) {
    const time = this._timeAtCoordinate(x);
    if (time == null) {
      return null;
    }
    const series = [];
    const levels = [];
    const pricePane = this._h._pricePaneIndex();
    for (const slot of this._h._instances.values()) {
      if (this._h._slotPaneIndex(slot) !== pricePane) {
        continue;   // オシレーターペインの値は価格ではない（55 を価格として吸うと桁が変わる）。
      }
      for (const v of this._slotValues(slot, (s) => pointValueAt(s, time))) {
        if (Number.isFinite(v.value)) {
          series.push({ kind: 'series', label: v.name, price: v.value });
        }
      }
      if (slot.visible === false) {
        continue;   // 水準線は _slotValues を通らない＝可視の判定をここでも行う（描画と一致させる）。
      }
      for (const h of slot.hlinePayloads ?? []) {
        if (h && Number.isFinite(h.price)) {
          levels.push({ kind: 'level', label: h.text ?? '', price: h.price });
        }
      }
    }
    const ohlc = [];
    const candle = pointAtTime(this._h.getCandles(), time);
    if (candle && candle.open !== undefined) {
      for (const label of ['open', 'high', 'low', 'close']) {
        ohlc.push({ kind: 'ohlc', label, price: candle[label] });
      }
    }
    // 並びが解決の優先順（スナップ解決器は同距離で先頭を採る）。指標系列＝クリックの狙い、
    //   水準線＝明示的に置かれた参照、OHLC＝常に在る背景、の順に置く。
    return [...series, ...levels, ...ohlc];
  }

  // x 座標（チャート要素基準）が指す足の time。範囲外・非対応環境（Fake/SSR）は null。
  //   バンドル実測（v5.2.0）: `coordinateToTime` は座標→バー index（Math.ceil）→ 元の time
  //   （originalTime）へ写す。データ範囲外の index は null を返す＝足の無い所では開かない。
  _timeAtCoordinate(x) {
    if (!Number.isFinite(x) || typeof this._h._chart.timeScale !== 'function') {
      return null;
    }
    const ts = this._h._chart.timeScale();
    if (!ts || typeof ts.coordinateToTime !== 'function') {
      return null;
    }
    const t = ts.coordinateToTime(x);
    return t == null ? null : t;
  }

  // sessions の time→{poc,vah,val} Map を供給する（読み取り欄で当日 MP を出す）。null で非表示。
  //   lwc へは触れない純データ受け渡し（actor が sessions 応答から構築して渡す）。
  setSessionMP(map) {
    this._sessionMP = (map && typeof map.get === 'function') ? map : null;
  }
}
