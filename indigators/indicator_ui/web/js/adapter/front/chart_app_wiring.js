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
import { ClipboardGateway } from './clipboard_gateway.js';
import { createCopyBarInfoItem } from './copy_bar_info_item.js';
import { indicatorHeading } from './bar_info_text.js';
import { createChartWithMainSeries, makeUpdatePaneHeight } from './chart_bootstrap.js';
import { ScrollToLatestButton } from './scroll_to_latest_button.js';
import { TimeframeMenu, timeframeLabels } from './timeframe_menu.js';
import { ChartTemplateMenu } from './chart_template_menu.js';
import { ChartTemplateDialogs } from './chart_template_dialogs.js';
import { ChartTemplateController } from './chart_template_controller.js';
import { ColorThemeMenu } from './color_theme_menu.js';
import { ColorThemeDialogs } from './color_theme_dialogs.js';
import { TF_BAR_SEC } from '../../domain/tf_meta.js';
import { installChartToolbar, installIndicatorDialog, CHART_SYMBOL } from './app_chrome_view.js';
import { ChromeThemeApplier } from './chrome_theme_applier.js';
import { LocalStorageThemeGateway } from './local_storage_theme_gateway.js';
import { ColorThemeController, COLOR_THEME_HOST_CONTRACT, loadThemeState } from './color_theme_controller.js';
import { createHostView } from './host_view.js';
import { resolveAllChrome } from '../../usecase/color_resolver.js';

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
  // チャート生成（組み立て点）。生成オプション・メイン系列は共有ヘルパ chart_bootstrap（ISSUE-123）。
  const { chart, mainSeries } = createChartWithMainSeries({ lwc, container });
  // ポート実装: ComputeHttpClient（fetch /compute）。candles は /candles から取得する。
  const compute = new ComputeHttpClient({ fetch });

  // 左上オーバーレイ・スタックの 2 欄。器は各 View が版面配下へ自分で生成し所有する
  //   （ISSUE-277 の残 / ISSUE-278 #16: 配信 3 ページへの手書き複製を撤去）。
  //   **構築順が DOM の並び**になるため「現在値 → 読み取り欄」の順に作る（従来 HTML と同じ並び）。
  //   現在値の大型表示そのものは維持する（依頼者判断 2026-08-07）。
  const currentPriceView = new CurrentPriceView({ document: doc, elementId: 'current-price' });
  const readoutView = new CrosshairReadoutView({ document: doc, elementId: 'crosshair-readout' });
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
  };
}

// controller に依存しない UI 部品の install（controller は遅延参照で受ける）。
//   時間足メニューの項目集合は台帳（domain/tf_meta.TF_CODES）から導出する＝手書きリストを持たない。
//   isVerticalPanBlocked: 縦パンを開始しない条件（MP リプレイ表示モード等）。未指定は従来どおり無条件。
//   getTemplates: テンプレート協働子の遅延参照（controller 生成後に代入されるため）。
//   getColorThemes: テーマ協働子の遅延参照（getTemplates と同一規約・同じ理由）。
export function installSharedUi({
  container, renderer, doc, getController, updatePaneHeight,
  isVerticalPanBlocked = undefined, getTemplates = () => null, getColorThemes = () => null,
  toolbar = {},
} = {}) {
  // アプリ外枠（ツールバー・指標ダイアログ）の DOM は View が所有し生成する（ISSUE-278 #16）。
  //   配信 3 ページへ同じマークアップを手書き複製する義務を無くす（指標ダイアログは 3 ページで
  //   1440 文字が byte 一致していた＝純粋な三重複製）。ページに要求するのはアンカー #app だけ。
  //   controller.bind() より前（bootstrap 内）に生成する＝bind が要素を必ず見つける。
  installChartToolbar(doc, toolbar);
  installIndicatorDialog(doc, {});

  // チャート操作（縦価格パン・wheel 価格ズーム・dblclick reset）。振る舞い本体は当該 controller が所有。
  new ChartInteractionController({
    container, renderer, getController, updatePaneHeight, isVerticalPanBlocked,
  }).install();

  // ISSUE-116: 「最新のバーまでスクロール」ボタン（» ）。DOM 不在は install 内の防御で no-op。
  new ScrollToLatestButton({ container, renderer, document: doc }).install();

  // ユーザー指示 2026-08-09: ローソク足上の右クリックメニュー（「情報をコピーする」）。
  //   足の解決と値の取り出しは renderer（upstream 隔離点）、見出し（ラベル＋パラメータ）と時間足は
  //   controller（表示名・適用状態の単一情報源）、銘柄は app_chrome_view の CHART_SYMBOL
  //   （ツールバーと同一文字列）、書き込みは ClipboardGateway、告知は ChartToastView。
  //   メニューは項目の中身を知らない。controller は本関数の呼び出し時点では未生成のため遅延参照する。
  //   ユーザー指摘 2026-08-10: 値だけでは「どのチャート・どのパラメータの値か」が復元できないため、
  //   コピー時点の文脈をここで集めて渡す（貼り付け先には画面が無い）。
  const chartToast = new ChartToastView({ document: doc });
  const copyBarInfo = createCopyBarInfoItem({
    renderer,
    clipboard: new ClipboardGateway({ document: doc }),
    toast: chartToast,
    getContext: () => {
      const c = getController ? getController() : null;
      if (!c || typeof c.legendRows !== 'function') {
        return { symbol: CHART_SYMBOL };   // controller 未生成（最小 fake）＝銘柄だけで縮退。
      }
      return {
        symbol: CHART_SYMBOL,
        timeframe: c._timeframe,
        labels: new Map(c.legendRows().map((r) => [r.instanceId, indicatorHeading(r)])),
      };
    },
  });
  const chartContextMenu = new ChartContextMenu({
    document: doc, container, items: [copyBarInfo],
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

  return {
    chartTemplateMenu, chartTemplateDialogs, colorThemeMenu, colorThemeDialogs,
    chartContextMenu, chartToast,
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

// controller 生成後に結ぶ協働子（テンプレート協働子・取引密度帯・売買マーカー・現在値）。
//   onTimeframeChanged: 時間足購読へ追加で流すフック（live の tf-period 即時再適用など）。未指定は no-op。
export function wireControllerCollaborators({
  controller, renderer, doc, fetch, datasetRef, timeframe, recentBars,
  templateStore, chartTemplateMenu, chartTemplateDialogs,
  themeStore, themeState = null, chromeThemeApplier = null,
  colorThemeMenu = null, colorThemeDialogs = null, now = null,
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

  return {
    chartTemplates, tickvolBands, tradeMarkers, currentPriceView, colorThemes,
  };
}
