// chart_app_wiring.js — ライブ／リプレイ両 Composition Root が共有する配線の単一ソース（ISSUE-278 #4）。
//
// 旧状態: `simulator/replay_ui/.../composition_root_front.js` はライブ合成根の**全文フォーク**だった。
//   同じ配線が 2 か所に手書きで存在するため、ライブ側の修正がリプレイへ届かない。実際に取り残しが
//   3 回発生している（`#rp-mode` の option 欠落 4079461 ／ カテゴリボタン ISSUE-221 ／ 凡例の器
//   ISSUE-277）。さらにフォーク側の手書き定数が陳腐化していた（「30m 非対応」の 8 足リスト。実測では
//   リプレイ core の `/candles?timeframe=30m` も `/compute` も 200 を返す＝制約はとうに消えていた）。
//
// 本モジュールの役割: 「両者で同一の配線」をここに 1 つだけ持ち、各 root は **自分固有の差** だけを書く。
//   複製を消すのであって、分岐を足すのではない（root 固有の部品は引数で受けるか、root 側に残す）。
//
// 責務境界（SRP）:
//   - composeChartShell            : chart / renderer / 永続化 / catalog など「controller 以前」の組み立て。
//   - installSharedUi              : controller に依存しない UI 部品の install（操作・メニュー）。
//   - wireControllerCollaborators  : controller 生成後に結ぶ協働子（テンプレート・帯・マーカー・現在値）。
// いずれも DOM/ネットワークを直接触らず、注入された doc / fetch を各部品へ渡すだけにする（DIP）。

import { ChartRenderer } from './chart_renderer.js';
import { CrosshairReadoutView } from './crosshair_readout_view.js';
import { PaneLegendView } from './pane_legend_view.js';
import { PaneReorderDrag } from './pane_reorder_drag.js';
import { CurrentPriceView } from './current_price_view.js';
import { ComputeHttpClient } from './compute_http_client.js';
import { LocalStorageGateway } from './local_storage_gateway.js';
import { LocalStorageTemplateGateway } from './local_storage_template_gateway.js';
import { IndicatorCatalogClient } from './catalog_client.js';
import { TradeMarkersRenderer } from './trade_markers_renderer.js';
import { TickvolBandsActor } from './tickvol_bands_actor.js';
import { TickvolBandsController } from './tickvol_bands_controller.js';
import { ChartInteractionController } from './chart_interaction_controller.js';
import { ChartContextMenu } from './chart_context_menu.js';
import { ChartToastView } from './chart_toast_view.js';
import { setSeriesTimeGuardNotifier } from './series_time_guard.js';
import { ClipboardGateway } from './clipboard_gateway.js';
import { createCopyBarInfoItem } from './copy_bar_info_item.js';
import { indicatorHeading } from './bar_info_text.js';
import {
  createChartWithMainSeries, makeUpdatePaneHeight, makeMeasurePaneAreaHeight, installPaneGeometryFollow,
} from './chart_bootstrap.js';
import { ScrollToLatestButton } from './scroll_to_latest_button.js';
import { TimeframeMenu, timeframeLabels } from './timeframe_menu.js';
import { ChartTemplateMenu } from './chart_template_menu.js';
import { ChartTemplateDialogs } from './chart_template_dialogs.js';
import { ChartTemplateController } from './chart_template_controller.js';
import { ColorThemeMenu } from './color_theme_menu.js';
import { ColorThemeDialogs } from './color_theme_dialogs.js';
import { TF_BAR_SEC } from '../../domain/tf_meta.js';
import {
  installChartToolbar, installIndicatorDialog, setChartSymbol, chartSymbol,
} from './app_chrome_view.js';
import { ChromeThemeApplier } from './chrome_theme_applier.js';
import { LocalStorageThemeGateway } from './local_storage_theme_gateway.js';
import { ColorThemeController, COLOR_THEME_HOST_CONTRACT, loadThemeState } from './color_theme_controller.js';
import { createHostView } from './host_view.js';
import { resolveAllChrome } from '../../usecase/color_resolver.js';
// ISSUE-368 スライス 7: ポジションサイズ計算機一式（生成は共有配線が所有し、root は識別子だけを渡す）。
import { PositionSizingMenu } from './position_sizing_menu.js';
import { PositionSizingDialog, defaultParams, defaultLevels } from './position_sizing_dialog.js';
import { PositionSizingController } from './position_sizing_controller.js';
import { PriceLevelLinesPrimitive } from './price_level_lines_primitive.js';
import { PriceLevelDragController } from './price_level_drag_controller.js';
import { PricePickController } from './price_pick_controller.js';
import { McWorkerGateway } from './mc_worker_gateway.js';
import { createPriceContextItems, liveMenuItems } from './position_sizing_context_items.js';
import { resolvePickedPrice, MSG_NO_SYMBOL_SPEC } from './price_pick_resolver.js';
import { lookupSymbolSpec } from './symbol_spec_catalog.js';
import { PositionSizingPlanUseCase } from '../../usecase/position_sizing_plan.js';
import { createPriceLevels } from '../../domain/price_levels.js';

// ISSUE-383: 時系列契約防壁トーストの表示時間（ms）。文言に「詳細ログの場所」まで含むため
//   既定（1.6 秒）より長く取る（読み切れないと能動通知の意味がない）。
const SERIES_GUARD_TOAST_MS = 10000;

// GET /candles?datasetRef=&timeframe=&limit= で candles を取得する（B方式）。失敗時は null。
//   timeframe 省略時はサーバが原子（再集計なし）扱い、limit 省略時は全件（後方互換）。
export async function fetchCandles(fetchImpl, datasetRef = 'sample', timeframe = null, limit = null) {
  if (typeof fetchImpl !== 'function') {
    return null;
  }
  try {
    let url = `/candles?datasetRef=${encodeURIComponent(datasetRef)}`;
    if (timeframe) {
      url += `&timeframe=${encodeURIComponent(timeframe)}`;
    }
    if (limit) {
      url += `&limit=${encodeURIComponent(limit)}`;
    }
    const resp = await fetchImpl(url);
    if (!resp.ok) {
      return null;
    }
    const payload = await resp.json();
    return payload && payload.ok ? payload.candles : null;
  } catch {
    return null;
  }
}

// controller 以前の組み立て（チャート・描画・永続化・catalog）。両 root で完全に同一。
//   catalog.load は param 既定値と variant ごとの受理 param（ISSUE-092 ③ / ISSUE-278 #8）を
//   サーバから overlay する。controller 生成前に完了させ、以後のインスタンス生成が単一情報源の
//   既定値を用いるようにする（load は例外を投げない＝失敗時は静的既定へフォールバック）。
export async function composeChartShell({
  lwc, container, doc, storage, fetch, datasetRef, recentBars,
} = {}) {
  // 銘柄仕様（呼び値・表示桁）を datasetRef から引く。値の権威は marketdata 台帳ただ 1 つで、
  //   front は解決結果を**値として配るだけ**（ISSUE-368 S-6 / S-7 A-3）。引き当ては
  //   `lookupSymbolSpec`（front 配下で台帳へ触れる唯一の口）で、本モジュールの外へは出さない。
  //   ここと wireControllerCollaborators の 2 か所が同じ `datasetRef`（root が両方へ渡す同一の値）
  //   から同じ純関数を引く＝結果は必ず一致する（引き当ては台帳の凍結オブジェクトを読むだけで
  //   状態を持たない）。値を持ち回るための新しい配管（**root の引数追加**）は作らない。
  //   この一文の射程（工程 5 是正 A で明確化・事実に合わせた補足であって方針変更ではない）:
  //   戒めているのは「root の**入力**を増やして値を外から通す」形だけである。**戻り値へ足して
  //   root が転送する**形は既存の先例そのもの（`themeState`: 本関数が 1 回解決 → 戻り値 → root →
  //   `wireControllerCollaborators`）で、解決点は本関数の中に留まったままなので抵触しない。
  //   実際、解決済みの値を**画面の他の面へ配る**手段はこれしかない（下の `priceDigits` と同型）。
  const symbolSpec = lookupSymbolSpec(datasetRef);
  // チャート生成（組み立て点）。生成オプション・メイン系列は共有ヘルパ chart_bootstrap（ISSUE-123）。
  //   表示桁（priceFormat）は台帳の digits/tick に従わせる（A-3）。解決できなければ渡さない＝
  //   lwc 既定（precision=2 / minMove=0.01）のまま＝従来の挙動（front が桁を勝手に決めない）。
  const { chart, mainSeries } = createChartWithMainSeries({ lwc, container, symbolSpec });
  // ポート実装: ComputeHttpClient（fetch /compute）。candles は /candles から取得する。
  const compute = new ComputeHttpClient({ fetch });

  // 左上オーバーレイ・スタックの 2 欄。器は各 View が版面配下へ自分で生成し所有する
  //   （ISSUE-277 の残 / ISSUE-278 #16: 配信 3 ページへの手書き複製を撤去）。
  //   **構築順が DOM の並び**になるため「現在値 → 読み取り欄」の順に作る（従来 HTML と同じ並び）。
  //   現在値の大型表示そのものは維持する（依頼者判断 2026-08-07）。
  //   表示桁は既に解決済みの `symbolSpec` から**値として配る**（A-3 の「現在値」・S-6: 解決点は
  //   本モジュールの既存 2 か所だけで、View 側に新しい引き当てを作らない）。解決できなければ
  //   `null`＝従来の表示（無音で桁を決めない・価格軸 `priceFormat` と同じ態度）。
  const priceDigits = symbolSpec ? symbolSpec.digits : null;
  const currentPriceView = new CurrentPriceView({
    document: doc, elementId: 'current-price', priceDigits,
  });
  const readoutView = new CrosshairReadoutView({
    document: doc, elementId: 'crosshair-readout', priceDigits,
  });
  // ペイン別凡例（ISSUE-276）。描画先の器は View 自身が版面（.chart-wrap）配下へ生成する
  //   （HTML への直書き＝配信ページの手書き複製をやめた・ISSUE-277）。root は id 文字列を知らない。
  // 指標ペインの並べ替え（ドラッグ&ドロップ・ユーザー指示 2026-08-09）。凡例のチップを掴み手にし、
  //   実行は renderer の並べ替えポートへ委ねる（協働子は upstream を知らない）。renderer は直後に
  //   生成されるため、呼び出し時解決の関数で渡す（生成順序に依存させない）。
  const paneReorder = new PaneReorderDrag({
    document: doc, movePane: (from, to) => renderer.movePane(from, to),
  });
  const paneLegendView = new PaneLegendView({ document: doc, reorder: paneReorder });

  // ChartRenderer は upstream API の唯一の隔離点（系列追加系 API 名を root へ漏らさない）。
  const renderer = new ChartRenderer({
    chart, mainSeries, lwc, onCrosshairReadout: (dto) => readoutView.render(dto),
    onPaneLegend: (model) => paneLegendView.update(model),
  });

  // 指標カラーテーマ: クロム色を 2 機構（lwc オプション / :root の CSS カスタムプロパティ）へ
  //   配る協働子（基本設計_指標カラーテーマ.md §4.3・A-11）。JS 側の sink は ChartRenderer
  //   （upstream 隔離点）で、applier は lwc を知らない（§7.3 ISP/DIP）。
  const chromeThemeApplier = new ChromeThemeApplier({
    chromeSink: renderer,
    rootStyle: doc && doc.documentElement ? doc.documentElement.style : null,
  });
  // テーマ永続化（§4.9 の 2 キー）。接頭辞は注入された storage が付ける（gateway は付けない）。
  const themeStore = new LocalStorageThemeGateway(storage);
  // §5.5 起動時の復元: activeTheme.v1 を読み、dangling は「テーマ未選択」へ縮退して書き戻す
  //   （F-C6）。解決はここ 1 回だけで、協働子（wireControllerCollaborators）へはこの結果を
  //   そのまま渡す。2 度読むと縮退・lastSeq 復旧が片側にしか効かず、地の色と協働子が思って
  //   いる選択中テーマがずれる。
  const themeState = loadThemeState(themeStore);
  // 起動時に **1 度だけ** 配信する（chart 生成直後・§5.5）。テーマ未選択なら恒等（生成時
  //   オプションと同一の色）で、app.css が読む --ct-* も同時に供給される。ここで無条件に
  //   既定を配ってからテーマを配ると、2 度目の書き込みでちらつく（二重配信）。
  chromeThemeApplier.apply(resolveAllChrome(themeState.theme));

  // 価格軸ホイールズームの座標→価格変換に使う pane 高（container 高 - timeScale 高）を供給する。
  const updatePaneHeight = makeUpdatePaneHeight({ container, chart, renderer });
  updatePaneHeight();
  // ペイン幾何の派生（区切り高→凡例の位置・座標→ペイン判定）は **使う時点の実測**を根拠にする
  //   （ISSUE-440）。push だけだと、setPaneHeight を呼ばない経路（起動直後・区切りドラッグ・
  //   版面リサイズ）で古い総高から区切り高が逆算され、ラベルだけが数十 px ずれる。
  renderer.setPaneAreaHeightProvider(makeMeasurePaneAreaHeight({ container, chart }));
  // 版面の寸法が変わったら凡例を引き直す（下部ペインの分割線・ウィンドウのリサイズ）。
  //   幾何が変わっていなければ何も起きない（指紋比較のみ）。lwc の subscribeSizeChange は
  //   autoSize 由来のリサイズで発火しないことを実測したので、寸法は自分で観測する。
  installPaneGeometryFollow({ container, renderer });

  const persistence = new LocalStorageGateway(storage);
  // テンプレート永続化（§4.2 の 3 キー）。接頭辞は注入された storage が付ける（gateway は付けない）。
  const templateStore = new LocalStorageTemplateGateway(storage);
  const catalog = new IndicatorCatalogClient();
  await catalog.load(fetch);

  // 時間足切替で candles を再取得するためのローダ。controller.setTimeframe が (ref, tf) で呼ぶ。
  const loadCandles = (ref, tf) => fetchCandles(fetch, ref, tf, recentBars);

  return {
    chart, mainSeries, compute, readoutView, currentPriceView, paneLegendView, renderer,
    updatePaneHeight, persistence, templateStore, catalog, loadCandles, chromeThemeApplier,
    themeStore, themeState,
    // 解決済みの銘柄仕様（`themeState` と同型の転送）。root は中身を解釈せず `installSharedUi` へ
    //   渡すだけで、台帳を引き直さない（解決点は本関数と wireControllerCollaborators の 2 か所のまま）。
    symbolSpec,
  };
}

// controller に依存しない UI 部品の install（controller は遅延参照で受ける）。
//   時間足メニューの項目集合は台帳（domain/tf_meta.TF_CODES）から導出する＝手書きリストを持たない。
//   isVerticalPanBlocked: 縦パンを開始しない条件（MP リプレイ表示モード等）。未指定は従来どおり無条件。
//   getTemplates: テンプレート協働子の遅延参照（controller 生成後に代入されるため）。
//   getColorThemes: テーマ協働子の遅延参照（getTemplates と同一規約・同じ理由）。
//   symbolSpec: 解決済みの銘柄仕様 `{symbol, tick, digits}`（composeChartShell の戻り値を root が
//     そのまま転送する）。**既定値つきの任意引数**なので、渡さない既存の呼び出しは 1 バイトも
//     変わらない（従来どおり価格の桁を決めない）。ここでは解決しない＝解決点を増やさない。
export function installSharedUi({
  container, renderer, doc, getController, updatePaneHeight,
  isVerticalPanBlocked = undefined, getTemplates = () => null, getColorThemes = () => null,
  getPositionSizing = () => null,
  toolbar = {}, contextMenuItems = [], symbolSpec = null,
} = {}) {
  // アプリ外枠（ツールバー・指標ダイアログ）の DOM は View が所有し生成する（ISSUE-278 #16）。
  //   配信 3 ページへ同じマークアップを手書き複製する義務を無くす（指標ダイアログは 3 ページで
  //   1440 文字が byte 一致していた＝純粋な三重複製）。ページに要求するのはアンカー #app だけ。
  //   controller.bind() より前（bootstrap 内）に生成する＝bind が要素を必ず見つける。
  installChartToolbar(doc, toolbar);
  installIndicatorDialog(doc, {});

  // チャート操作（縦価格パン・wheel 価格ズーム・dblclick reset）。振る舞い本体は当該 controller が所有。
  //   ISSUE-368 スライス 3: 生成した実体を保持する（従来は install() 後に捨てていた）。
  //   縦パンを止めたい後発の協働子（水準線 drag）は controller 生成より後に結線されるため、
  //   登録口を戻り値で配る。root が自前で ChartInteractionController を new し直すのは
  //   `composition_roots_share_wiring.test.js` の SHARED_OWNED が禁じている＝配るのは共有配線の責務。
  const chartInteraction = new ChartInteractionController({
    container, renderer, getController, updatePaneHeight, isVerticalPanBlocked,
  });
  chartInteraction.install();

  // ISSUE-116: 「最新のバーまでスクロール」ボタン（» ）。DOM 不在は install 内の防御で no-op。
  new ScrollToLatestButton({ container, renderer, document: doc }).install();

  // ユーザー指示 2026-08-09: ローソク足上の右クリックメニュー（「情報をコピーする」）。
  //   足の解決と値の取り出しは renderer（upstream 隔離点）、見出し（ラベル＋パラメータ）と時間足は
  //   controller（表示名・適用状態の単一情報源）、銘柄は app_chrome_view の器
  //   （`chartSymbol(doc)`＝ツールバーが表示しているのと同一の実体。front は名前を自称しない・
  //   ISSUE-368 A-4）、書き込みは ClipboardGateway、告知は ChartToastView。
  //   メニューは項目の中身を知らない。controller は本関数の呼び出し時点では未生成のため遅延参照する。
  //   ユーザー指摘 2026-08-10: 値だけでは「どのチャート・どのパラメータの値か」が復元できないため、
  //   コピー時点の文脈をここで集めて渡す（貼り付け先には画面が無い）。
  const chartToast = new ChartToastView({ document: doc });
  // ISSUE-383（能動通知・ユーザー裁定 2026-08-17）: 時系列契約防壁の発火は console.error のみだと
  //   DevTools を開かない限り気づけず、発生源特定（残調査）の入口が失われる。版面トーストで
  //   「発生した事実」と「詳細ログの場所」を告知する（証跡本体は console.error＋op_log 側が持つ）。
  //   表示は長め（既定 1.6 秒ではログ場所まで読めない）。通知は guard 側 seam に登録＝依存方向は
  //   composition → guard の一方向（guard は View を知らない）。
  setSeriesTimeGuardNotifier((label) => {
    // __opsPrev は op_log（ISSUE-298・統合 UI のみ install）が提供する。無いページで案内すると
    //   嘘になるため、在るときだけ回収手段として文言に含める。
    const opsHint = (typeof globalThis !== 'undefined' && typeof globalThis.__opsPrev === 'function')
      ? '（リロード後は __opsPrev() で回収可）' : '';
    chartToast.show(
      `時系列データ異常を検出（ISSUE-383）: ${label} — 詳細: DevTools コンソール [series-time-guard]${opsHint}`,
      SERIES_GUARD_TOAST_MS,
    );
  });
  // 四本値の表示桁（工程 5 是正 A）。読み取り欄（`composeChartShell` の `priceDigits`）と**同じ
  //   解決結果**を配る＝コピーした文字列と画面表示が食い違わない（`format.js:17-18` が単一ソース化の
  //   根拠に掲げる不変条件）。解決できない構成では `null`＝従来どおり（無音で桁を決めない）。
  const priceDigits = symbolSpec ? symbolSpec.digits : null;
  const copyBarInfo = createCopyBarInfoItem({
    renderer,
    clipboard: new ClipboardGateway({ document: doc }),
    toast: chartToast,
    getContext: () => {
      const c = getController ? getController() : null;
      if (!c || typeof c.legendRows !== 'function') {
        // controller 未生成（最小 fake）＝銘柄と桁だけで縮退。
        return { symbol: chartSymbol(doc), priceDigits };
      }
      return {
        symbol: chartSymbol(doc),
        timeframe: c._timeframe,
        labels: new Map(c.legendRows().map((r) => [r.instanceId, indicatorHeading(r)])),
        priceDigits,
      };
    },
  });
  // ISSUE-368 スライス 8-c: root が渡した項目を**後ろに**足す（R-P3 の価格設定 3 項目）。
  //   共有配線が無条件に足すと replay まで項目が出る（＝replay 汚染）。逆に root で
  //   `new ChartContextMenu` すると contextmenu リスナーが 2 本になり、メニューが二重に出る。
  //   よって「メニューは共有・項目は注入」に保つ（ChartContextMenu 自体は 1 byte も変えない）。
  //   ISSUE-435: 一覧は**開くたびに読み直す**。ここで `[copyBarInfo, ...items]` と新しい配列へ
  //   写すと install 時点の内容が焼き付き、注入側の増減（設定済みの水準だけ出る解除項目）が
  //   永久に届かない（`chart_context_menu.js:34,122` は構築時の参照を開くたびに読む・実測）。
  //   静的な配列を渡す従来の呼び出しは、毎回同じ内容が組み直されるだけで挙動が変わらない。
  const chartContextMenu = new ChartContextMenu({
    document: doc, container, items: liveMenuItems(() => [copyBarInfo, ...(contextMenuItems ?? [])]),
  });
  chartContextMenu.install();

  // ISSUE-117: 時間足ドロップダウンの開閉制御（選択・active 同期は bind() の data-timeframe 配線）。
  //   項目集合は既定＝台帳導出（ISSUE-278 #4: リプレイ側の手書き 8 足を撤去。実測でリプレイ core も
  //   30m の /candles・/compute を 200 で返すため、そもそも制約が存在しない）。
  new TimeframeMenu({ document: doc }).install();

  // チャートテンプレートのメニュー・ダイアログ（§6.1・§6.2）。項目 DOM は共有 JS が生成し、
  //   index.html には空マウント（#tpl-menu）のみを置く。メニューは協働子を import せず
  //   コールバック注入で結ぶ（DIP）。協働子は controller 生成後に代入されるため遅延参照する。
  const chartTemplateDialogs = new ChartTemplateDialogs({ document: doc });
  const chartTemplateMenu = new ChartTemplateMenu({
    document: doc,
    // U6: 開くたびに最新のビューモデルで再描画する（restore() との順序依存を作らない）。
    provide: () => { const t = getTemplates(); return t ? t.viewModel() : {}; },
    onSelect: (templateId) => { const t = getTemplates(); return t ? t.applyTemplate(templateId) : undefined; },
    onSave: () => { const t = getTemplates(); return t ? t.openSaveDialog() : undefined; },
    onBind: (templateId) => { const t = getTemplates(); return t ? t.bindCurrentTimeframe(templateId) : undefined; },
    onManage: () => { const t = getTemplates(); return t ? t.openManageDialog() : undefined; },
  });
  chartTemplateMenu.install();

  // 指標カラーテーマのメニュー・ダイアログ（基本設計_指標カラーテーマ §6.1〜§6.3・§7.1）。
  const { menu: colorThemeMenu, dialogs: colorThemeDialogs } = createColorThemeUi(doc, getColorThemes);

  // ポジションサイズ計算機のツールバー入口とモーダル（ISSUE-368 スライス 6/7）。
  //   協働子は wireControllerCollaborators で生成されるため getPositionSizing() で遅延参照する
  //   （テンプレート・テーマと同一規約。未結線のうちは押しても何も起きず例外も出ない）。
  const { menu: positionSizingMenu, dialog: positionSizingDialog } = createPositionSizingUi(doc, getPositionSizing);

  return {
    chartTemplateMenu, chartTemplateDialogs, colorThemeMenu, colorThemeDialogs,
    positionSizingMenu, positionSizingDialog,
    chartContextMenu, chartToast,
    // ISSUE-368 スライス 3: 縦パンブロッカーの登録口（解除関数を返す）。
    registerVerticalPanBlocker: (predicate) => chartInteraction.addVerticalPanBlocker(predicate),
  };
}

// テーマのメニュー・ダイアログを組み立てて install する（installSharedUi から 1 回だけ呼ぶ）。
//   器（#color-theme-menu）は installChartToolbar が生成済みで、項目 DOM は共有 JS が生成する。
//   menu / dialogs は協働子を import せず、コールバック注入だけで結ぶ（DIP）。協働子は controller
//   生成後（wireControllerCollaborators）に確定するため `getColorThemes()` で遅延参照する
//   （未結線のうちは全コールバックが no-op＝押しても何も起きない・例外を投げない）。
//   この形はテンプレート側の `getTemplates()` と同一で、両者に別々の受け渡し機構を作らない。
function createColorThemeUi(doc, getColorThemes) {
  const dialogs = new ColorThemeDialogs({ document: doc });
  const menu = new ColorThemeMenu({
    document: doc,
    // 開くたびに最新のビューモデルで再描画する（起動時の復元との順序依存を作らない）。
    provide: () => { const c = getColorThemes(); return c ? c.viewModel() : {}; },
    // UC-C02 適用（themeId=null は「テーマなし（既定色）」）。適用後の一覧の描き直しは協働子が行う。
    onSelect: (themeId) => { const c = getColorThemes(); return c ? c.applyTheme(themeId) : undefined; },
    // UC-C01 作成・保存 ／ UC-C03 改名・削除。ダイアログの開閉手順は協働子が所有する
    //   （§5.1・§5.3 の手順が menu 側と協働子側の 2 箇所に散らない）。
    onCreate: () => { const c = getColorThemes(); return c ? c.openCreateDialog() : undefined; },
    onManage: () => { const c = getColorThemes(); return c ? c.openManageDialog() : undefined; },
  });
  menu.install();
  return { menu, dialogs };
}

// 計算機のメニュー・モーダルを組み立てて install する（installSharedUi から 1 回だけ呼ぶ）。
//   器（#position-sizing-menu）は installChartToolbar が生成済みで、項目 DOM は各モジュールが作る。
//   menu / dialog は協働子を import せず、コールバック注入だけで結ぶ（color_theme と同一の形）。
function createPositionSizingUi(doc, getPositionSizing) {
  const of = () => getPositionSizing();
  const dialog = new PositionSizingDialog({
    document: doc,
    onChangeParams: (patch) => { const c = of(); return c ? c.setParams(patch) : undefined; },
    onChangeLevels: (spec) => { const c = of(); return c ? c.setLevels(spec) : undefined; },
    onRun: () => { const c = of(); return c ? c.runMonteCarlo() : undefined; },
    onRequestPick: (target) => { const c = of(); return c ? c.requestPick(target) : undefined; },
    // 閉じたらアームも解除する（残すと抑止が掛かったまま解除手段が画面から消える・Y-1）。
    onClose: () => { const c = of(); return c ? c.cancelPick() : undefined; },
    // アーム中バーの [取消]（画面から解除できる手段・裁定 2026-08-20）。
    onCancelPick: () => { const c = of(); return c ? c.cancelPick() : undefined; },
    // 手入力の確定 → 欄の表示をモデル値へ合わせ直す（D-3・裁定 2026-08-20）。
    onCommitPrices: () => { const c = of(); return c ? c.commitPrices() : undefined; },
  });
  const menu = new PositionSizingMenu({
    document: doc,
    onOpen: () => { const c = of(); return c ? c.open() : undefined; },
  });
  menu.install();
  return { menu, dialog };
}

// controller 生成後に結ぶ協働子（テンプレート協働子・取引密度帯・売買マーカー・現在値）。
//   onTimeframeChanged: 時間足購読へ追加で流すフック（live の tf-period 即時再適用など）。未指定は no-op。
export function wireControllerCollaborators({
  controller, renderer, doc, fetch, datasetRef, timeframe, recentBars,
  templateStore, chartTemplateMenu, chartTemplateDialogs,
  themeStore, themeState = null, chromeThemeApplier = null,
  colorThemeMenu = null, colorThemeDialogs = null, now = null,
  positionSizingDialog = null, registerVerticalPanBlocker = null, chartToast = null,
  lwc, mainSeries, chart, container, currentPriceView,
  onTimeframeChanged = () => {},
} = {}) {
  // 指標カラーテーマの協働子（§7.1）。host は全体ではなく ThemeHost 契約の射影を渡す
  //   （§7.3 ISP・宣言だけで施行しない状態を作らない）。起動時に解決済みのテーマ状態は
  //   composeChartShell が持っており、ここで読み直さない（二重読み・二重縮退の防止）。
  //
  // themeStore 未注入時の縮退: 協働子を生成せず選択中テーマの供給も結ばない
  //   （＝テーマなし＝既定色。既存挙動と完全に同一）。単体テスト・後方互換の呼び出し面のため。
  //
  //   本番の結線: 両 composition_root_front.js は composeChartShell の戻り値も本関数の引数も
  //   **明示列挙**で受け渡ししている。これは templateStore / chartTemplateMenu が既に取っている
  //   受け渡し規約と同一で、root が足すのは識別子だけ（`themeStore, themeState,
  //   chromeThemeApplier` と `colorThemeMenu, colorThemeDialogs`）。配線のロジックそのものは
  //   本関数 1 箇所に留まり、root へ複製されない。
  const colorThemes = themeStore
    ? new ColorThemeController(
      createHostView(controller, COLOR_THEME_HOST_CONTRACT),
      {
        gateway: themeStore,
        chromeApplier: chromeThemeApplier,
        state: themeState,
        // メニュー・ダイアログ（installSharedUi が生成・install 済み）を協働子へ結ぶ。ここで初めて
        //   行クリック → applyTheme ／ 作成 → saveTheme ／ 管理 → renameTheme / deleteTheme が生きる。
        menu: colorThemeMenu,
        dialogs: colorThemeDialogs,
        now,
      },
    )
    : null;
  if (colorThemes) {
    // 選択中テーマの供給ポートを結ぶ（共有配線 1 箇所・root へ同一 1 行を複製しない）。
    //   controller は毎回協働子へ問い合わせる＝適用のたびに provider の戻り値が追随する
    //   （値を焼き付けない）。
    controller.setColorThemeProvider(() => colorThemes.activeTheme());
    // 結んだ直後に一覧を描き直し、起動時の選択中テーマが選択状態（is-active）で出るようにする。
    colorThemes.render();
  }

  // テンプレート協働子（§7.1）。有効時間足集合は台帳が単一情報源（domain/tf_meta.js の TF_BAR_SEC＝
  //   LAYERING_CONVENTIONS「UI の時間足ボタン集合もこの集合から乖離させない」）。
  const chartTemplates = new ChartTemplateController(controller, {
    gateway: templateStore,
    menu: chartTemplateMenu,
    dialogs: chartTemplateDialogs,
    validTimeframes: Object.keys(TF_BAR_SEC),
    // 保存ダイアログの文言用ラベル写像（§6.2）。単一情報源は timeframe_menu.js の groups。
    timeframeLabels: timeframeLabels(),
  });
  // 時間足切替への介入（§7.2）: 購読スロット（setTimeframeObserver）は単数かつ売買マーカーで
  //   使用済み（E-7）のため使わず、own property での差し替えで行う。順序（除去 → 切替 → 適用）と
  //   再入防止は協働子が所有する。
  const proceedSetTimeframe = controller.setTimeframe.bind(controller);
  const proceedTemplateTimeframe = (tf) => chartTemplates.onTimeframeChange(tf, proceedSetTimeframe);

  // 取引密度帯（時刻帯の背景色）。アクター駆動型のためレジストリへ登録する（台帳 1 行追記で完結）。
  //   getUntil: リプレイは単一時計 to（controller._untilTime）＝当日を集計に含めない因果窓の基準。
  //   ライブは _untilTime 非在席（undefined）＝null を返す＝サーバの現在時刻。
  const tickvolBands = new TickvolBandsActor({
    fetch, datasetRef, renderer,
    getTimeframe: () => controller._timeframe,
    getUntil: () => (controller._untilTime != null ? controller._untilTime : null),
  });
  controller.registerActorController('tickvol_bands', new TickvolBandsController(controller, tickvolBands));
  // 時間足切替: 帯は時間足に依存しない（サーバは常に 1 分足原子で集計）ので再取得せず、塗る足だけ
  //   引き直す。テンプレート介入の**内側へ**チェーンする（既存の介入順序を壊さない）。
  controller.setTimeframe = (tf) => {
    const done = proceedTemplateTimeframe(tf);
    tickvolBands.onTimeframeChange();
    return done;
  };
  // リプレイ時計の前進: セッション日が変わったときだけ再取得する（日内は応答不変＝当日非参照）。
  if (typeof controller.setUntilTime === 'function') {
    const proceedUntil = controller.setUntilTime.bind(controller);
    controller.setUntilTime = (t) => {
      const done = proceedUntil(t);
      tickvolBands.onClock();
      return done;
    };
  }

  // 売買マーカー重畳。副作用 fetch は増やさず renderer を返すのみ（load トリガは入口が呼ぶ）。
  const tradeMarkers = new TradeMarkersRenderer({
    lwc, mainSeries, chart, chartRenderer: renderer, document: doc, container,
  });
  // 現在値の大型表示（#current-price）は composeChartShell が構築済み（欄の並びを構築順で決める）。
  //   candle 変更 observer は単一スロットのため、tradeMarkers への通知と同一コールバック内で
  //   現在値ビューと帯の塗り直しも行う。
  renderer.setCandleObserver(() => {
    tradeMarkers.onCandlesChanged();
    currentPriceView.render(renderer.lastClose());
    // 足の差し替え（時間足・期間プリセット・カレンダー・リビール）で塗る足を引き直す。
    tickvolBands.onCandlesChanged();
  });

  // ペイン並び順の永続化（ユーザー指示「永続化しろ」2026-08-09）。ドラッグで並べ替えたら、
  //   その順序を applied 配列の順序として state へ確定し保存する（保存キーは増やさない。
  //   復元は従来どおり applied 配列順に pane を作り直すため、これだけで並びが再現する）。
  renderer.setPaneOrderObserver((instanceIds) => controller.applyPaneOrder(instanceIds));

  // 指標の追加・削除で pane（と pane 内の系列）が作り直されるため、背景プリミティブを張り直す。
  //   購読スロットは単数で、後から別の購読者が入る。上書きで本フックが消えないよう
  //   setAppliedObserver 自体を合成する（後続購読者の挙動は不変・解除も従来どおり）。
  const proceedSetAppliedObserver = controller.setAppliedObserver.bind(controller);
  proceedSetAppliedObserver(() => tickvolBands.onPanesChanged());
  controller.setAppliedObserver = (observer) => proceedSetAppliedObserver(() => {
    if (typeof observer === 'function') {
      observer();
    }
    tickvolBands.onPanesChanged();
  });

  // 時間足変更を売買マーカーへ通知し、該当時間足（建玉の時間足）以外は非表示にする。
  tradeMarkers.setCurrentTimeframe(timeframe);
  controller.setTimeframeObserver((tf) => {
    tradeMarkers.setCurrentTimeframe(tf);
    onTimeframeChanged(tf);
  });

  // ポジションサイズ計算機の協働子（ISSUE-368 スライス 7）。モーダルが注入されている構成でだけ
  //   組む（未注入の最小構成・単体テストでは従来どおり何も生えない）。
  // 銘柄仕様（呼び値）を引き、以後は**値として配る**（resolver へ／初期水準の `createPriceLevels`
  //   へ／モーダルの `step` へ）。配る先が自分で引き直すと「どの銘柄の刻みで丸めたか」が経路ごとに
  //   割れるため、本関数より下では二度と引かない（設計「追補: 工程 2」E-3・S-6 通過条件）。
  //
  // 引き当ての**呼び出し**は本モジュール内に 2 か所ある（`composeChartShell` と本関数）。同一の
  //   `datasetRef`（root が両方へ渡す同じ値）に対する同一の純関数呼び出しで、`lookupSymbolSpec` は
  //   凍結された生成物を読むだけで状態を持たないため、結果は必ず一致する。
  //   `themeState` と同型の「起動時に解決した値を root 経由で転送する」形（:357 参照）へ寄せる案は
  //   **本ブランチでは実施しない**（工程 5 是正 5-2 で下記のとおり記述を訂正した）。
  //   従来ここには「引数へ移すと既存 30 件が赤になる／テストの期待値を書き換えずには成立しない」と
  //   書いていたが、**それは誤り**である: `symbolSpec = lookupSymbolSpec(datasetRef)` を
  //   **既定値つきの任意引数**にすれば（JS の分配既定値は先行する束縛を参照できる）、`datasetRef`
  //   だけを渡す既存の呼び出しは 1 バイトも変わらない。加えて 2 か所の呼び出しは同一 `datasetRef`
  //   に対する同一の純関数呼び出しであり、結果は必ず一致する（上記のとおり状態を持たない）。
  //   見送る理由は「成立しないから」ではなく、明示転送への移行が本ブランチの是正範囲外だからである。
  const symbolSpec = lookupSymbolSpec(datasetRef);
  // 銘柄名（表示）も同じ解決結果から配る（ISSUE-368 A-4）。器はツールバーが持ち、中身は
  //   「どのデータセットを見ているか」を知っている本関数が入れる（tf-menu / tpl-menu と同じ
  //   「器は View・中身は所有者」の分離）。解決できなければ縮退表示になる＝**無音で空にしない**。
  //   ツールバーが無い構成（最小 fake・SSR）では no-op。
  setChartSymbol(doc, symbolSpec ? symbolSpec.symbol : null);
  const positionSizing = positionSizingDialog
    ? createPositionSizingCollaborators({
      renderer,
      container,
      doc,
      dialog: positionSizingDialog,
      registerVerticalPanBlocker,
      toast: chartToast,
      symbolSpec,
      datasetRef,
    })
    : null;

  return {
    chartTemplates, tickvolBands, tradeMarkers, currentPriceView, colorThemes, positionSizing,
  };
}

// 計算機の協働子一式を組む（水準線 primitive・drag・ピッカー・MC Worker・usecase・協働子）。
//
//   ここで一括して組む理由: これらは互いの識別子を必要とする（drag は「いまの水準」を協働子から
//   得る／ピッカーの確定はモーダルへ書き戻す／協働子は primitive とモーダルへ配る）。root へ
//   ばらすと、同じ結線を 2 つの root へ手書き複製することになる（ISSUE-278 #4 が撤去した状態）。
//
//   スライス 4 の未結線（`new PriceLevelDragController` の呼び出し 0 件）はここで解消される。
function createPositionSizingCollaborators({
  renderer, container, doc, dialog, registerVerticalPanBlocker, toast, symbolSpec, datasetRef,
}) {
  // 呼び値。解決できなければ null＝**丸めない**のではなく「チャートからの価格指定を落とす」
  //   （設計「フェイルセーフ」: 値ではなく機能を落とし理由を出す）。告知はトースト（利用者が
  //   実際に使おうとした時点）と console.error（開発者向けの証跡）の両方で出す。前者だけだと
  //   原因（どの ref か）が残らず、後者だけだと DevTools を開かない限り気づけない。
  //   `symbolSpec` の真偽だけを見てよい理由（不変条件）: `lookupSymbolSpec` は「量子化に使えない
  //   刻みを持つ台帳」を解決成功と扱わない（`symbol_spec_catalog.js` の 3 段目）。したがって
  //   ここへ届く `symbolSpec` の `tick` は必ず正の有限数で、この面に検算を第 2 実装として置かない。
  const tick = symbolSpec ? symbolSpec.tick : null;
  if (!symbolSpec) {
    // eslint-disable-next-line no-console
    console.error(`[position-sizing] 銘柄仕様が解決できません（datasetRef=${datasetRef}）: ${MSG_NO_SYMBOL_SPEC}`);
  }
  // モーダルの価格欄の刻み（経路 7）。解決できなければ従来どおり step='any'（手入力は落とさない）。
  dialog?.setSymbolSpec?.(symbolSpec);
  // 水準線の描画先（メイン系列の背景 primitive・§6）。装着時にクロム色が 1 回配られる。
  const primitive = new PriceLevelLinesPrimitive();
  // 線に添える価格の表示桁（ISSUE-435 実装 2）。モーダルの欄（上の setSymbolSpec）・ピッカーの
  //   ゴースト（下の spec）と**同じ解決結果**を配る＝同じ価格が面ごとに違う文字列にならない。
  primitive.setSymbolSpec?.(symbolSpec);
  if (renderer && typeof renderer.attachBackgroundPrimitive === 'function') {
    renderer.attachBackgroundPrimitive('position_sizing', () => primitive);
  }

  // 初期状態はモーダルの定義表から導出する（画面の初期表示と計算の初期値を食い違わせない）。
  //   水準の保持者は usecase 1 か所（協働子へ写しを渡さない＝TC-PC14）。
  //   刻みを注入すると「刻み上にない価格は PriceLevels に存在できない」が不変条件になる
  //   （E-02・S-4）。resolver を通らない水準線 drag（経路 6）もここを通るため迂回できない。
  const usecase = new PositionSizingPlanUseCase({
    mcPort: new McWorkerGateway(),
    levels: createPriceLevels({ ...defaultLevels(), tick }),
    params: defaultParams(),
  });

  // ピッカーの確定はモーダルへ書き戻す（`controller` は直後に確定する＝呼び出し時解決）。
  const picker = new PricePickController({
    container,
    renderer,
    document: doc,
    registerVerticalPanBlocker,
    onConfirm: (target, price) => controller.confirmPick(target, price),
    // アーム中はモーダルがチャートを覆ってはならない（実 UI 実測 2026-08-20: backdrop が
    //   ビューポート全面のままで elementFromPoint がモーダルを返し、R-P1 が成立しなかった）。
    onArmChange: (armed, target) => controller.setPicking(armed, target),
    // 解決済みの銘柄仕様を**値として配る**（S-6: 解決点は本関数の 1 か所だけ）。ピッカーが
    //   これを resolver へ転送することで、右クリックと同じ規則・同じ引数で価格が決まる（D-1）。
    //   表示桁（digits）もここから届く＝ゴーストの書式が台帳に従う（D-2）。
    spec: symbolSpec,
  });
  picker.install();

  const controller = new PositionSizingController({
    usecase, dialog, picker, primitive, toast, symbolSpec,
  });

  // 水準線 drag（スライス 4）。掴む対象の座標源は primitive、水準の実体は協働子から得る。
  const drag = new PriceLevelDragController({
    container,
    renderer,
    primitive,
    getLevels: () => controller.levels(),
    onLevelsChange: (next) => controller.applyLevels(next),
    registerVerticalPanBlocker,
    // ピッカーのアーム中は掴ませない（入力先は常に一意＝R-P1。アーム中に別の水準線を
    //   掴むと「利確を指定していたのに損切りが動く」が起きる・工程 5 🔴-2 で再現）。
    //   銘柄仕様が解決できないときも掴ませない（フェイルセーフ: 経路 6 も落とす。掴めてしまうと
    //   刻みの分からない価格を drag で作れる＝機能だけが無音で生き残る）。
    isGrabBlocked: () => picker.isArmed() || !symbolSpec,
    // 掴めなかった理由の告知（工程 5 🟡-2）。設計「フェイルセーフ」は経路 6（drag）にも
    //   「トーストで告知」を課しているが、判定だけがあって告知が無かった。
    //   **鳴らすのは刻みが不明なときだけ**: アーム中は入力先が一意であること自体が意図した
    //   状態で、掴もうとするたびに鳴らすと連打で鳴り続ける（裁定どおり無告知のまま）。
    //   文言はピッカー・右クリックと同じ単一ソースから取る（写しを作らない）。
    onGrabBlocked: () => {
      if (!symbolSpec) {
        toast?.show?.(MSG_NO_SYMBOL_SPEC);
      }
    },
  });
  drag.install();

  return {
    controller, primitive, picker, drag, usecase,
  };
}

/**
 * 右クリックメニューへ載せる価格設定 3 項目を作る（ISSUE-368 スライス 8-c・R-P3）。
 *
 * root が `installSharedUi({ contextMenuItems })` へ渡す（共有配線が無条件に足すと replay まで
 * 項目が出るため）。座標→価格の解決はピッカーと同一の 1 本（`resolvePickedPrice`）を使う。
 *
 * 告知先（案内トースト）は **遅延参照**で受ける。共有トースト `chartToast` は `installSharedUi` の
 * 内側で生成されるため、その引数（本関数の戻り値）を組み立てる時点では root から参照できない。
 * 値で受けると root は `null` を渡すしかなく、下段ペインの右クリックが**無音**になる
 * （裁定「オシレーターペイン上のクリックは無効化＋案内」の未達・2026-08-20 に実際に発生していた）。
 * 遅延 getter は `getPositionSizing` / `getTemplates` / `getColorThemes` と同一規約で、
 * 受け渡し機構を新設しない。
 *
 * @param {object} deps
 * @param {object} deps.renderer ChartRenderer。
 * @param {Function} deps.getPositionSizing 協働子の遅延参照（生成前は null を返してよい）。
 * @param {Function} [deps.getToast] 案内表示の遅延参照（下段ペイン・価格が取れない座標）。
 */
export function createPositionSizingContextItems({
  renderer, getPositionSizing, getToast = () => null,
}) {
  const of = () => getPositionSizing();
  // 銘柄仕様は**協働子から遅延参照する**（ISSUE-368 スライス S-6）。本関数は root が呼ぶため
  //   datasetRef を受け取っておらず、ここで自分で解決すると解決点が 2 つになる（＝経路ごとに
  //   違う刻みで丸まりうる）。解決は wireControllerCollaborators の 1 回だけで、その結果を
  //   協働子が保持している。既存の遅延参照（getPositionSizing）に相乗りする＝新しい配管を作らない。
  //   協働子が未生成（配線途中）なら null＝フェイルクローズ（確定させない）。
  const specOf = () => { const c = of(); return c && typeof c.symbolSpec === 'function' ? c.symbolSpec() : null; };
  // 一覧は**開くたびに組み直す**（ISSUE-435）。root は本関数を起動時に 1 回しか呼ばず、その戻り値が
  //   `ChartContextMenu` に握られ続ける（`composition_root_front.js:286` / `chart_context_menu.js:34`）。
  //   解除項目は「いま設定済みの水準」で増減するので、1 回きりのスナップショットでは永久に出ない。
  return liveMenuItems(() => createPriceContextItems({
    resolvePrice: (context) => resolvePickedPrice({
      renderer,
      x: context ? context.x : undefined,
      y: context ? context.y : undefined,
      spec: specOf(),
    }),
    onSetStop: (price) => { const c = of(); return c ? c.setStopPrice(price) : undefined; },
    onAddEntry: (price) => { const c = of(); return c ? c.addEntryPrice(price) : undefined; },
    onSetTake: (price) => { const c = of(); return c ? c.setTakePrice(price) : undefined; },
    // 解除項目（ISSUE-435 実装 1）。**いまの水準**を協働子から遅延参照する（水準の保持者は
    //   usecase 1 か所で、ここへ写しを置かない＝TC-PC14 と同じ規律）。協働子が未生成なら
    //   null＝解除項目が 1 つも出ない（未結線の状態で「押しても何も起きない項目」を出さない）。
    onClear: (target) => { const c = of(); return c ? c.clearPrice(target) : undefined; },
    getLevels: () => { const c = of(); return c && typeof c.levels === 'function' ? c.levels() : null; },
    // 呼ばれた時点で告知先を解決する（未生成・DOM 不在なら告知しない＝例外を投げない）。
    //   銘柄仕様が解決できていないときは**その理由へ差し替える**: このとき機能全体が無効なので、
    //   座標ごとの理由（「価格が取れません」「価格チャート上で…」）を出すと、利用者は
    //   「別の場所を押せば入る」と誤解して押し続ける（無音ではないが誤った案内になる）。
    toast: {
      show: (message) => {
        const t = getToast();
        if (t && typeof t.show === 'function') {
          t.show(specOf() ? message : MSG_NO_SYMBOL_SPEC);
        }
      },
    },
  }));
}
