// PaneGeometryController（adapter/front/pane_geometry_controller.js）— ペイン幾何ロールの協働クラス
// @upstream-isolation: pane_geometry_controller.js
//   （ISSUE-479 Wave2 J-2: chart_renderer.js から 1:1 抽出。ScaleController / CandleFeed /
//    SeriesDrawer と同形＝生 host 参照を受け取り、host の共有状態を読み書きする）。
//
// 担う関心は 1 つ:「ペインの幾何（何面あるか・どこからどこまでか・どれが動かせるか）を測り、
//   その従属変数である凡例 DTO を配る」。upstream（IPaneApi: panes / getHeight / paneIndex /
//   getPane / moveTo）へ触れるのはこのロールに閉じ、View へは数値と文字列だけを渡す。
//
// 状態の所有（ISSUE-181「状態も一緒に移す」）: ペインの安定採番（_paneKeys/_paneKeySeq）・
//   並び順購読者（_onPaneOrder）・版面総高の測定関数（_paneAreaHeightProvider）・最後に配った
//   幾何の指紋（_lastPaneGeometrySig）・再確認の予約フラグ（_geometryRecheckPending）・
//   利用者が決めた配分とその観測時の総高（_paneGoal/_lastPaneArea/_appliedPaneHeights）は
//   **本クラスが所有する**。ChartRenderer 側にはこれらのフィールドを残さない。
//
// host に残る共有状態（本クラスは読むだけ）: _chart / _mainSeries / _instances /
//   _paneHeight（縦パンの px→価格換算にも使うため ScaleController と共有）/ _onPaneLegend。

// 版面の増減を指標ペインで吸収するときの下限（ISSUE-440(2)・依頼者裁定 2026-08-21）。
//   これ未満へ詰めると軸ラベルも凡例も読めなくなるので、指標側で吸収し切れないぶんだけ
//   価格ペインが譲る（版面が極端に低いときに価格ペインを 0 にしないための床）。
const MIN_INDICATOR_PANE_PX = 40;
const MIN_PRICE_PANE_PX = 60;

/**
 * 小数の配分を**合計を保ったまま**整数へ丸める（最大剰余法・ISSUE-442）。
 *
 * 単純な四捨五入だと合計が版面とずれ、ずれたぶんだけ lwc の実高が小数になって、そこから
 * 派生する凡例の位置と 1px 食い違う（実測 2026-08-22: ペイン上端 373 に対しラベル 374）。
 */
export function roundKeepingSum(values, total) {
  const floors = values.map((v) => Math.floor(v));
  let rest = total - floors.reduce((a, b) => a + b, 0);
  // 端数の大きい順に 1px ずつ配る（同値なら添字の若い方＝上のペインから）。
  const order = values
    .map((v, i) => ({ i, frac: v - Math.floor(v) }))
    .sort((a, b) => (b.frac - a.frac) || (a.i - b.i));
  for (const { i } of order) {
    if (rest <= 0) break;
    floors[i] += 1;
    rest -= 1;
  }
  return floors;
}

export class PaneGeometryController {
  // host: ChartRenderer インスタンス（_chart / _mainSeries / _instances / _paneHeight /
  //   _onPaneLegend の所有者。協働子間の直接依存は作らず、必ず host 経由で辿る）。
  constructor(host) {
    this._h = host;
    // ペインの**位置に依らない安定 ID**（ISSUE-341）。並べ替えを入れた結果 paneIndex は
    //   「今どこに居るか（位置）」しか表さなくなったため、「どのペインか（同一性）」を別に持つ。
    //   pane オブジェクトを鍵にした WeakMap＝ペインが消えれば採番も一緒に消える（後始末が要らない）。
    //   実測（vendor/lightweight-charts.js v5.2.0）: chart.panes() は内部ペインごとに生成した
    //   ラッパを `fb()` がキャッシュして返すため、同じペインには毎回同じオブジェクトが返る。
    //   moveTo（並べ替え）は内部配列の順序だけを変えるのでラッパの同一性は保たれる。
    this._paneKeys = new WeakMap();
    this._paneKeySeq = 0;
    // ペイン並び順の変化を受け取る購読者（既定 no-op＝後方互換）。setPaneOrderObserver で結ぶ。
    this._onPaneOrder = () => {};
    // ペイン領域の総高を「その場で測る」関数（setPaneAreaHeightProvider で供給・ISSUE-440）。
    //   未供給なら host._paneHeight へ縮退する（既存の呼び出しは不変）。
    this._paneAreaHeightProvider = null;
    // 最後に凡例 DTO を配ったときのペイン幾何の指紋（refreshPaneLegendIfGeometryChanged 用）。
    this._lastPaneGeometrySig = null;
    // 次フレームでの幾何突き合わせを予約済みか（多重予約を作らない・_scheduleGeometryRecheck）。
    this._geometryRecheckPending = false;
    // 利用者が最後に決めたペイン配分（総高が変わらないあいだの実測）。総高が変わったとき、
    //   価格ペインをこの高さへ戻すための目標にする（ISSUE-440(2)）。
    this._paneGoal = null;
    // 目標を控えた時点の版面総高。これと違う総高を観測したら「利用者以外の要因」と判定する。
    this._lastPaneArea = null;
    // 自分が配り直した高さ（利用者の意思と区別するための印）。
    this._appliedPaneHeights = null;
  }

  // ペイン別凡例 DTO を構築してコールバックへ渡す（ISSUE-276）。
  //   発行のたびに幾何の指紋を控える。どの経路で発行されても「最後に配った幾何」が 1 つに
  //   決まるので、refreshPaneLegendIfGeometryChanged が二重発行にならない（ISSUE-440）。
  _emitPaneLegend(param = null) {
    this._lastPaneGeometrySig = this._paneGeometrySignature();
    this._h._onPaneLegend(this.paneLegendModel(param));
    this._scheduleGeometryRecheck();
  }

  // 配った直後の幾何は**まだ確定していないことがある**（ISSUE-440）。ペインの増減は
  //   lightweight-charts が次の描画で高さを配り直すため、指標を適用した瞬間に発行した DTO は
  //   古い高さで組まれている。実測 2026-08-21: 起動直後（マウス操作なし）の凡例が
  //   ペイン上端 558/745px に対し 698/930px に出たまま動かなかった（マウスを動かすと直る
  //   ＝発行の契機が無いだけで、位置の規則は正しい）。
  //   よって発行のたびに**次フレームで突き合わせ**、変わっていれば配り直す。変わっていなければ
  //   何も起きないので、通常のクロスヘア移動で余計な再描画は生まれない（多重予約もしない）。
  _scheduleGeometryRecheck() {
    const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : null;
    if (!raf || this._geometryRecheckPending) {
      return;
    }
    this._geometryRecheckPending = true;
    raf(() => {
      this._geometryRecheckPending = false;
      // 幾何の突き合わせと同時に「利用者が決めた配分」の控えも更新する（ISSUE-440(2)）。
      //   起動直後はペインが増えるたびに高さが確定し直すので、ここで控えないと最初の
      //   版面変化のときに目標が無い（＝lwc の比率保持のまま価格ペインが縮む）。
      this.syncPaneGeometry();
    });
  }

  // 指標ペインの並べ替え（ドラッグ&ドロップの着地点・ユーザー指示 2026-08-09）。
  //
  //   upstream の並べ替え API（IPaneApi.moveTo）を呼ぶ唯一の点。バンドル実測（v5.2.0）で
  //   `moveTo(to)` は `splice(from,1)` → `splice(to,0,pane)` の **抜いて差し込む** 意味であり、
  //   上下どちらへ動かしても「to 番の位置へ入る」で一意に決まる（swapPanes＝単純交換とは別物）。
  //
  //   価格ペイン（メイン系列が居るペイン）は移動元にも移動先にもしない。overlay 指標の系列は
  //   `chart.addSeries(...)`（既定 paneIndex=0）で追加されるため、価格ペインを 0 番から動かすと
  //   以後の overlay 指標が別ペインへ落ちる（実装上の前提が崩れる）。指示の対象は指標ペインで
  //   あり、価格ペインを固定しておけば前提と指示の双方を満たす。
  //
  //   @returns {boolean} 実際に並べ替えたら true（不正な指定・移動不能は false＝呼び出し側は無視してよい）。
  movePane(fromIndex, toIndex) {
    if (typeof this._h._chart.panes !== 'function') {
      return false;
    }
    const panes = this._h._chart.panes() ?? [];
    const from = Number(fromIndex);
    const to = Number(toIndex);
    if (!Number.isInteger(from) || !Number.isInteger(to) || from === to) {
      return false;
    }
    if (from < 0 || from >= panes.length || to < 0 || to >= panes.length) {
      return false;
    }
    if (!this._isPaneMovable(from, panes.length) || !this._isPaneMovable(to, panes.length)) {
      return false;
    }
    const pane = panes[from];
    if (!pane || typeof pane.moveTo !== 'function') {
      return false;
    }
    pane.moveTo(to);
    // ペイン構成が変わる（index と top がずれる）ため凡例を引き直す（remove() と同じ規律）。
    this._emitPaneLegend(null);
    // 並び順の変化を購読者（＝状態を持つ controller）へ通知する。並び順の保存は state 側の
    //   関心であり、本 class は「いまこの順である」という事実を渡すだけ（永続化は知らない）。
    this._onPaneOrder(this.paneOrderInstanceIds());
    return true;
  }

  // ペイン並び順の変化を購読する（composition が controller の永続化へ結ぶ・省略時 no-op）。
  setPaneOrderObserver(fn) {
    this._onPaneOrder = typeof fn === 'function' ? fn : () => {};
  }

  // ペイン別凡例の DTO（幾何＋値）を返す。**upstream に触れるのはここだけ**で、View へは
  //   数値・文字列だけを渡す（§2.2 隔離）。
  //
  //   { groups: [{ paneIndex, top, height, movable, rows: [{ instanceId, values: [{name,value,color}] }] }] }
  //
  //   top はチャート要素上端からの px。lightweight-charts は各ペインを 1px の区切りで縦に積むため
  //   （実測 2026-08-06: paneSize=[497,166,165] / チャート高 858 / 時間軸 28 → 残り 2px が区切り 2 本）、
  //   区切り高は「チャート高 − 時間軸 − ペイン高合計」をペイン間の数で割って求める。値を定数で
  //   持たない（upstream のスタイル変更で静かにずれるのを避ける）。
  //
  //   valuePickerFor（任意・ユーザー指示 2026-08-09）: slot ごとの値取り出し方（Strategy）。既定は
  //   「クロスヘア位置の値・無ければ保持した最新値」＝従来の凡例規約。指定した足の情報を
  //   取り出す `barInfoAt` は、ここへ「その足の値だけを返す（最新値へ落ちない）」picker を渡す。
  //   在席集合・ペイン分類・並び・可視の扱いは本メソッド 1 か所に保つ（値の出所だけを差し替える）。
  paneLegendModel(param = null, valuePickerFor = null) {
    const seriesData = (param && param.seriesData) || null;
    const heights = this._paneHeights();
    const tops = this._paneTops(heights, this._paneSeparatorPx(heights));
    const byPane = new Map();
    for (const [instanceId, slot] of this._h._instances) {
      const paneIndex = this._slotPaneIndex(slot);
      if (!byPane.has(paneIndex)) {
        byPane.set(paneIndex, []);
      }
      const pick = typeof valuePickerFor === 'function'
        ? valuePickerFor(slot)
        : (series, key) => this._h._crosshairValue(slot, series, key, seriesData);
      byPane.get(paneIndex).push({ instanceId, values: this._h._slotValues(slot, pick) });
    }
    const paneKeys = this._paneKeysOrdered();
    const groups = [];
    for (const [paneIndex, rows] of byPane) {
      groups.push({
        paneIndex,
        // 位置に依らないペインの同一性（ISSUE-341）。折りたたみ状態など「ペインについて回る」
        //   ものはこちらを鍵にする。位置の情報（top/height/movable）は paneIndex 側のまま。
        paneKey: paneKeys[paneIndex] ?? null,
        top: tops[paneIndex] ?? 0,
        height: heights[paneIndex] ?? 0,
        // 掴んで動かせるか（凡例の見た目＝掴める合図はこの 1 値だけで決まる）。判定の単一情報源は
        //   _isPaneMovable で、movePane の受理判定と同じものを使う（affordance と実際の可否を割らない）。
        movable: this._isPaneMovable(paneIndex, heights.length),
        rows,
      });
    }
    groups.sort((a, b) => a.paneIndex - b.paneIndex);
    return { groups };
  }

  // ペインの安定 ID を paneIndex 順の配列で返す（ISSUE-341）。価格ペインも含めて全ペインへ採番する。
  //   初めて見たペインに 'p1','p2',… を振り、以後そのペインには同じ ID を返す。番号は**採番順**
  //   であって位置ではない（並べ替えても振り直さない＝それが「位置に依らない」の意味）。
  //   panes() 非提供の環境（Fake/SSR）は空配列＝ID なしで縮退し、View 側が paneIndex へ退避する。
  _paneKeysOrdered() {
    if (typeof this._h._chart.panes !== 'function') {
      return [];
    }
    const panes = this._h._chart.panes() ?? [];
    return panes.map((pane) => {
      if (!pane || typeof pane !== 'object') {
        return null;
      }
      let key = this._paneKeys.get(pane);
      if (!key) {
        this._paneKeySeq += 1;
        key = `p${this._paneKeySeq}`;
        this._paneKeys.set(pane, key);
      }
      return key;
    });
  }

  // 各ペインの高さ（px・ペイン順）。非提供環境（Fake/SSR）は空配列＝幾何なしで縮退する。
  //
  //   高さは **pane オブジェクトの getHeight()** から採る。`chart.paneSize(index)` は
  //   ペインの追加・削除の直後に内部状態が過渡的になると `Value is undefined` を投げ、その例外が
  //   凡例の更新経路ごと中断させた（実測 2026-08-06: 指標 7 件の連続適用で 6 回発生し、
  //   凡例が 1 ペインぶんしか描かれなかった）。index を介した逆引きをやめれば過渡状態に依存しない。
  _paneHeights() {
    if (typeof this._h._chart.panes !== 'function') {
      return [];
    }
    const panes = this._h._chart.panes() ?? [];
    return panes.map((pane) => {
      const h = (pane && typeof pane.getHeight === 'function') ? pane.getHeight() : 0;
      return Number.isFinite(h) ? h : 0;
    });
  }

  // ペイン領域の総高（container 高 − 時間軸高）。**測れるなら必ず測り直す**（ISSUE-440）。
  //   保持値（host._paneHeight・setPaneHeight で push される）は、push しない経路（起動直後・
  //   ペイン区切りのドラッグ・版面のリサイズ）で古いままになる。総高がずれると下の区切り高が
  //   ずれ、凡例の位置とクリック→ペイン判定が同じだけ狂う（実測 2026-08-21: 起動直後の凡例が
  //   正位置より 42px 下、区切りドラッグ後は 100px 下）。供給者（composition root）が測る関数を
  //   渡していればそれを毎回呼ぶ＝幾何は「使う時点の実測」だけを根拠にする。
  _paneAreaHeight() {
    if (typeof this._paneAreaHeightProvider === 'function') {
      const measured = this._paneAreaHeightProvider();
      if (Number.isFinite(measured) && measured > 0) {
        return measured;
      }
    }
    return this._h._paneHeight > 0 ? this._h._paneHeight : 0;
  }

  /**
   * ペイン領域の総高を「その場で測る」関数を供給する（composition root が結ぶ）。
   * 未供給なら従来どおり保持値（setPaneHeight）を使う＝既存の呼び出しは 1 バイトも変わらない。
   */
  setPaneAreaHeightProvider(fn) {
    this._paneAreaHeightProvider = typeof fn === 'function' ? fn : null;
  }

  // ペイン間の区切り高（px）。lightweight-charts はペインを 1px 前後の区切りで積むが、その値は
  //   upstream のスタイル由来なので定数で持たない。「ペイン領域の総高 − 各ペイン高の合計」を
  //   ペイン間の数で割って実測から求める。総高は _paneAreaHeight()（実測優先）から取る。
  //   求まらない環境では 0（数 px のズレはチップ位置として無害・例外を出す側へは倒さない）。
  _paneSeparatorPx(heights) {
    const area = this._paneAreaHeight();
    if (heights.length < 2 || !(area > 0)) {
      return 0;
    }
    const sum = heights.reduce((a, b) => a + b, 0);
    const rest = area - sum;
    // **切り捨てる**（ISSUE-442）。差分には区切り以外の 1px（枠線など）も混じり得るので、
    //   割り切って四捨五入すると区切りを実際より厚く見積もり、凡例の上端が 1px 下へずれる
    //   （実測 2026-08-22: 区切りの実測 1px に対し推定 1.5px → ラベル 374 / ペイン上端 373）。
    //   薄く見積もる側へ倒せば、ずれても内側（ペインの中）に留まる。
    return rest > 0 ? Math.floor(rest / (heights.length - 1)) : 0;
  }

  // いまのペイン幾何を表す指紋（高さの並び＋領域総高）。値が変わったときだけ凡例を引き直す
  //   ための比較用で、DTO の再構築より桁違いに安い（数値の連結だけ）。
  _paneGeometrySignature() {
    return `${this._paneHeights().join('/')}|${Math.round(this._paneAreaHeight())}`;
  }

  /**
   * 版面の総高が変わったとき、**利用者が決めた配分の比を保ったまま**全ペインを伸縮させる
   * （ISSUE-442・依頼者裁定 2026-08-22）。
   *
   * 経緯: 前の規則（価格ペインの px を保ち、差分を指標ペインへ配る・ISSUE-440(2)）は、
   *   面積が大きく減る場面（sim を開くと版面 928→472px）で**指標ペインを下限 40px まで潰した**。
   *   価格 557px を保つと指標側に 80px しか残らないためで、開くたびに手で広げる作業が要った。
   *   比で伸縮すれば全ペインが同じ割合で譲るので調整作業が要らず、面積が戻れば元の px へ戻る。
   *
   * 下限は安全弁として残す（版面が極端に低いときに 0 へ潰さない）。下限に当たったペインは
   *   その高さで固定し、残りを他のペインへ**同じ比**で配り直す。
   *
   * @returns {boolean} 高さの割り当てを変えたか
   */
  _applyGoalRatios(area, heights, goal) {
    const sum = (xs) => xs.reduce((a, b) => a + b, 0);
    // 区切りの総高は「総高 − 各ペイン高の合計」。ペインへ配れるのは残りだけ。
    const avail = sum(heights);
    const goalSum = sum(goal);
    if (!(avail > 0) || !(goalSum > 0)) {
      return false;
    }
    const priceIdx = this._pricePaneIndex();
    const floorOf = (i) => (i === priceIdx ? MIN_PRICE_PANE_PX : MIN_INDICATOR_PANE_PX);
    // 比で配る → 下限を割ったペインを固定 → 残りを未固定のペインへ同じ比で配り直す。
    //   固定は 1 回増えるごとに配れる量が減るので、変化が無くなるまで（高々ペイン数）繰り返す。
    const targets = goal.map(() => 0);
    const fixed = goal.map(() => false);
    for (let pass = 0; pass <= goal.length; pass += 1) {
      const freeIdx = goal.map((_, i) => i).filter((i) => !fixed[i]);
      const freeSpace = avail - goal.reduce((acc, _h, i) => acc + (fixed[i] ? targets[i] : 0), 0);
      const freeGoal = sum(freeIdx.map((i) => goal[i]));
      let changed = false;
      for (const i of freeIdx) {
        const share = freeGoal > 0 ? freeSpace * (goal[i] / freeGoal) : freeSpace / freeIdx.length;
        if (share < floorOf(i)) {
          targets[i] = floorOf(i);
          fixed[i] = true;
          changed = true;
        } else {
          targets[i] = share;
        }
      }
      if (!changed) break;
    }
    // 整数へ丸める（合計は保つ・最大剰余法）。小数のまま配ると lwc の実高も小数になり、
    //   凡例の上端（丸めた整数）と 1px ずれる（実測 2026-08-22: ペイン上端 373 に対し
    //   ラベル 374）。配る側で整数にしておけば、派生する位置も一致する。
    const rounded = roundKeepingSum(targets, Math.round(avail));
    // 1px 未満の差で毎フレーム書き換えない（描画のばたつきを作らない）。
    if (rounded.every((h, i) => Math.abs(h - heights[i]) < 1)) {
      return false;
    }
    // 高さの比＝ストレッチ比。lightweight-charts はペインを比で配るので、目標高をそのまま
    //   比として与えれば（合計が版面と一致するため）目標どおりの px になる。
    const panes = typeof this._h._chart.panes === 'function' ? this._h._chart.panes() : [];
    let applied = false;
    panes.forEach((pane, i) => {
      if (pane && typeof pane.setStretchFactor === 'function' && rounded[i] > 0) {
        pane.setStretchFactor(rounded[i]);
        applied = true;
      }
    });
    // 自分が配った値の印（次の観測でこれと一致する高さは「利用者の意思」ではない）。
    this._appliedPaneHeights = applied ? rounded : null;
    return applied;
  }

  /**
   * 幾何を実測へ揃える（総高が変わっていれば再配分し、変わっていれば凡例を配り直す）。
   *
   * 呼ぶのは版面の寸法変化の観測点（installPaneGeometryFollow）。区切りドラッグのように
   *   総高が変わらない変更では再配分せず、利用者が決めた高さを**目標として控える**だけにする。
   *
   * @returns {boolean} 凡例を配り直したか
   */
  syncPaneGeometry() {
    const area = this._paneAreaHeight();
    const heights = this._paneHeights();
    if (area > 0 && heights.length > 0) {
      const areaChanged = this._lastPaneArea !== null && this._lastPaneArea !== area;
      if (areaChanged && this._paneGoal && this._paneGoal.length === heights.length && heights.length >= 2) {
        // 総高が変わった＝利用者以外の要因（下部ペイン・ウィンドウ）。目標の**比**へ寄せ直す。
        //   目標そのものは書き換えない（面積が戻ったときに元の px へ戻すため）。
        this._applyGoalRatios(area, heights, this._paneGoal);
        this._lastPaneArea = area;
      } else {
        this._notePaneGeometry();
      }
    }
    return this.refreshPaneLegendIfGeometryChanged();
  }

  // 総高が変わっていないあいだの高さ＝**利用者が決めた配分**として控える（ISSUE-440(2)）。
  //   自分が配り直した直後の値は控えない（それは利用者の意思ではない）。控えてしまうと、
  //   版面が戻ったときに「詰められた高さ」が正解として復元され、元の配分へ戻らなくなる。
  _notePaneGeometry() {
    const area = this._paneAreaHeight();
    if (!(area > 0)) {
      return;
    }
    const heights = this._paneHeights();
    if (heights.length === 0) {
      return;
    }
    // 自分が配った状態のままなら控えない（詰めた高さを「利用者が決めた配分」にしない）。
    const isOurs = this._appliedPaneHeights
      && this._appliedPaneHeights.length === heights.length
      && this._appliedPaneHeights.every((h, i) => Math.abs(h - heights[i]) <= 2);
    if (isOurs) {
      this._lastPaneArea = area;
      return;
    }
    if (this._lastPaneArea === null || this._lastPaneArea === area) {
      this._paneGoal = heights;
      this._appliedPaneHeights = null;
    }
    this._lastPaneArea = area;
  }

  /**
   * ペイン幾何が前回発行時から変わっていれば、凡例 DTO を作り直す（変わっていなければ何もしない）。
   *
   * なぜ要るか（実測 2026-08-21・ISSUE-440）: 凡例の位置はペイン幾何の従属変数なのに、
   *   再発行の契機が「データ・構成・クロスヘア」しか無かった。ペイン区切りのドラッグと版面の
   *   リサイズはそのどれでもないため、**ラベルだけが古い位置に取り残される**（実測: 区切りを
   *   100px 上へ引いてもラベルは動かず、ペイン上端 458px に対しラベル 558px）。
   *   「幾何が動いたら引き直す」を成立させる呼び出し口がこれである。
   *
   * @returns {boolean} 引き直したか
   */
  refreshPaneLegendIfGeometryChanged() {
    const sig = this._paneGeometrySignature();
    if (sig === this._lastPaneGeometrySig) {
      return false;
    }
    this._emitPaneLegend(null);
    return true;
  }

  // 各ペインの上端 y（チャート要素基準）を paneIndex 順で返す。
  //   ペイン幾何の派生規則（上端＝それより上のペイン高と区切り高の累積）を持つのは**ここだけ**。
  //   凡例のチップ位置（paneLegendModel の group.top）と、座標→ペイン判定
  //   （paneIndexAtCoordinate）は同じ幾何を見る必要がある。累積の式を各所に書くと、
  //   区切り高の扱いが片方だけ変わったときに「凡例は正しいのにクリック判定だけずれる」
  //   （＝下段ペインのクリックを価格として受けてしまう）状態を作れてしまう。
  _paneTops(heights, separator) {
    const tops = [];
    let acc = 0;
    for (let i = 0; i < heights.length; i += 1) {
      tops.push(acc);
      acc += heights[i] + separator;
    }
    return tops;
  }

  /**
   * 現在のペイン順に並んだ **pane 指標の instanceId** を返す（ユーザー指示「永続化しろ」2026-08-09）。
   *
   * 並び順を保存する側（usecase）は、それを applied 配列の順序として持つ。その入力となる
   * 「いまの実際の並び」を答えるのが本メソッドで、upstream（pane.paneIndex）に触れるのは
   * 従来どおり本ロールに閉じる。
   *
   * overlay 指標（専用 pane を持たない＝価格ペイン）は含めない（並べ替えの対象外）。
   * 既に外されたペイン（paneIndex() が -1）も含めない。除去途中の slot を混ぜると、
   * 存在しない並びを保存して次回復元を壊す（`_pricePaneIndex` の -1 除外と同じ理由）。
   */
  paneOrderInstanceIds() {
    const withPane = [];
    for (const [instanceId, slot] of this._h._instances) {
      if (!slot || !slot.pane || typeof slot.pane.paneIndex !== 'function') {
        continue;
      }
      const idx = slot.pane.paneIndex();
      if (Number.isInteger(idx) && idx >= 0) {
        withPane.push({ instanceId, idx });
      }
    }
    return withPane.sort((a, b) => a.idx - b.idx).map((e) => e.instanceId);
  }

  // slot が属するペイン番号（overlay＝専用 pane を持たない指標は 0＝価格ペイン）。
  _slotPaneIndex(slot) {
    if (slot.pane && typeof slot.pane.paneIndex === 'function') {
      const idx = slot.pane.paneIndex();
      return Number.isFinite(idx) ? idx : 0;
    }
    return 0;
  }

  // メイン系列（ローソク）が居るペインの番号。価格ペインは並べ替えの対象外（movePane 参照）。
  //   番号を 0 と決め打たず upstream へ問う（将来 addPane 順が変わっても判定がずれない）。
  //   getPane 非提供の環境（Fake・旧版）は 0（生成時の既定ペイン）へ縮退する。
  //
  //   受理するのは **0 以上の整数だけ**（🟡-3 是正 2026-08-09）。バンドル実測（v5.2.0）で
  //   `paneIndex()` は `hf(t){return this.od.indexOf(t)}` 由来であり、内部配列に無いペインでは
  //   **-1** を返す。-1 は Number.isFinite を通ってしまい、価格ペイン番号として受理すると
  //   `_isPaneMovable` の `paneIndex !== this._pricePaneIndex()` が全ペインで真になる
  //   ＝禁じているはずの価格ペイン移動が通る（ガードがフェイルオープンする）。
  _pricePaneIndex() {
    const ms = this._h._mainSeries;
    if (ms && typeof ms.getPane === 'function') {
      const pane = ms.getPane();
      if (pane && typeof pane.paneIndex === 'function') {
        const idx = pane.paneIndex();
        if (Number.isInteger(idx) && idx >= 0) {
          return idx;
        }
      }
    }
    return 0;   // 範囲外（-1＝未登録）・非整数は既定ペインへ縮退する。
  }

  // 当該ペインを並べ替えられるか。価格ペインは対象外、指標ペインが 1 つだけなら動かす先が無い。
  //   paneCount 未指定時は upstream へ問い直す（凡例 DTO は算出済みの本数を渡して二度引きを避ける）。
  _isPaneMovable(paneIndex, paneCount = null) {
    const total = paneCount == null ? this._paneHeights().length : paneCount;
    if (total < 3) {
      return false;   // 価格ペイン＋指標ペイン 1 つ以下＝入れ替える相手が居ない。
    }
    return paneIndex !== this._pricePaneIndex();
  }

  /**
   * ISSUE-368 スライス 8-b: y 座標が属するペイン番号（**必須のガード**）。
   *
   * なぜ要るか（設計書「ピッカー経路の実測検証」2）: vendor 実測で `coordinateToPrice` は
   *   クランプ無しの線形外挿であり、オシレーターペインを押しても「価格」が返る（異常値）。
   *   ピッカーは「価格ペインを押したときだけ」価格を受け取る必要がある。
   *
   * 幾何の出所はペイン別凡例と同一にする（上端の算出は `_paneTops` 1 か所）。ここで
   *   累積を書き直すと、凡例の座標系と食い違う第 2 実装になる。
   *
   * @param {number} y チャート要素の左上基準の y（px）。
   * @returns {number|null} ペイン領域の外（時間軸・負値）と非対応環境（panes 非提供）は null。
   */
  paneIndexAtCoordinate(y) {
    if (!Number.isFinite(y)) {
      return null;
    }
    const heights = this._paneHeights();
    const tops = this._paneTops(heights, this._paneSeparatorPx(heights));
    for (let i = 0; i < heights.length; i += 1) {
      if (y >= tops[i] && y < tops[i] + heights[i]) {
        return i;
      }
    }
    return null;   // 区切り上・時間軸・領域外は「どのペインでもない」（価格を作らない）。
  }
}
