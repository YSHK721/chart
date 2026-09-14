// SeriesStyleApplier（adapter/front/series_style_applier.js）— 系列スタイル適用ロールの協働子
//   （ISSUE-479 Wave2 J-1 SRP: indicator_controller.js から 1:1 抽出）。
//
// 担う関心は 1 つ:「保存済みスタイル（AppliedInstance.styles）と選択中テーマから、実描画系列へ
//   配る色・幅・線種・可視を決めて renderer へ渡す」。色の**決定**は usecase の純関数
//   （color_resolver）が持ち、本クラスは「どの入力を集めて誰へ渡すか」だけを担う。
//
// 状態の所有（ISSUE-181「状態も一緒に移す」）: テーマ供給ポート（_colorThemeProvider）は
//   **本クラスが所有する**。IndicatorController 側はフィールドを持たず、公開面
//   （setColorThemeProvider / _applyStoredStyles / _activeColorTheme / _applyLevelLineColor）
//   だけを薄い委譲で温存する。
//
// ISP（ISSUE-099 🟡-4 / ISSUE-255）: host の広い公開面ではなく STYLE_HOST_CONTRACT の射影
//   （createHostView）だけを受け取る。契約外へ触れると実行時に例外になる。

import { reconcileSeriesStyles } from '../../usecase/facade.js';
import { expandSeriesNamePattern } from './series_name_matcher.js';
import {
  resolveSeriesColor, resolveInstanceTimeframe, buildColorRoleIndex,
} from '../../usecase/color_resolver.js';
import { ColorRole } from '../../domain/color_roles.js';

/**
 * SeriesStyleApplier（系列スタイル適用ロール）が host に要求する最小契約。
 *
 * @typedef {object} SeriesStyleHost
 * @property {{applied: Array}} _state   適用済みインスタンスを保持する純状態オブジェクト（read）。
 * @property {object} _renderer          スタイル適用先の renderer（applySeriesStyle / getSeriesStyles / applyLevelLineColor）。
 * @property {Map} _meta                 instanceId -> { def } 描画済みメタ（read）。
 * @property {{get: function}} _catalog  指標定義カタログ（read: get）。
 * @property {string} _timeframe         現在の表示時間足（テーマの時間足別解決に使う・read）。
 * @property {function} _paramsObject    params（配列/オブジェクト）を平坦オブジェクトへ正規化する。
 * @property {function} _commitState     協働子が算出した次 state を確定する（直接代入の代替）。
 */

// SeriesStyleHost 契約の実体列挙（構造充足テスト・依存面部分集合テストの固定点）。
export const STYLE_HOST_CONTRACT = Object.freeze({
  role: 'SeriesStyleHost',
  methods: Object.freeze(['_paramsObject', '_commitState']),
  fields: Object.freeze(['_state', '_renderer', '_meta', '_catalog', '_timeframe']),
  optionalFields: Object.freeze([]),
});

export class SeriesStyleApplier {
  /**
   * @param {SeriesStyleHost} host スタイル適用ロール契約を満たす host 射影。
   * @param {{colorThemeProvider?: ?function}} [opts] テーマ供給ポート（()->COLOR_THEME|null）。
   */
  constructor(host, { colorThemeProvider = null } = {}) {
    this._host = host;
    // 選択中の指標カラーテーマを供給するポート（基本設計_指標カラーテーマ.md §7.3 ISP）。
    //   ()->COLOR_THEME|null。本クラスはテーマの保存・採番・UI を一切知らず、色を解決する
    //   その瞬間の値だけを受け取る。未注入は「テーマなし」＝恒等（既定状態の見た目は不変）。
    this._colorThemeProvider = typeof colorThemeProvider === 'function' ? colorThemeProvider : null;
  }

  // AppliedInstance.styles（系列名 -> {color?,width?,style?,visible?}）を renderer へ適用する。
  //   未保存（null/空）や renderer 非対応（後方互換 Fake/SSR）は no-op。
  //   ISSUE-110 🔴-1: 適用前に現在の実系列名集合と突合し、実系列に存在しない stale キー
  //   （tgp の q_low/q_high や profit_band の probabilities 変更で系列が改名された等）を
  //   state から剪定する（無反映キーの永続蓄積と params 復帰時の意図せぬ復活を遮断）。
  //   実系列集合が取得不能・空のときは判定不能のため剪定しない（reconcile 側で防御）。
  //   基本設計_指標カラーテーマ.md §7.2 S2(a)（A-6）: **色の決定だけ**を resolver 経由へ差し替える。
  //   適用点を新設せず本メソッドに相乗りする理由は、描画完了の後段が既にここ 1 点へ集約済みで
  //   （E-9・series_render_router.js:103）、再計算・復元・時間足切替のどの経路からも必ず通るため。
  //   協働子が独自に完了時点を検知する方式（S1）は検知漏れの経路で色が戻る。
  //
  //   色の書き手はここ 1 箇所（R-1）。width / style / visible / display は従来どおり styles から
  //   そのまま流し、色だけを resolver の結果で上書きする。
  //   テーマ未設定・個別上書き無しのとき resolver は payload 色（baseColor）を返すため、
  //   既定状態の描画色は本差し替えの前後で完全に一致する（段階 2 通過条件 1）。
  _applyStoredStyles(instanceId) {
    const inst = this._host._state.applied.find((i) => i.instanceId === instanceId);
    if (!inst || typeof this._host._renderer.applySeriesStyle !== 'function') {
      return;
    }
    // 実描画系列のメタ（name と不変の baseColor）。後方互換 Fake/SSR は空配列で、その場合は
    //   色を解決する材料が無いため保存済み patch を従来どおり逐語で適用する。
    const metas = typeof this._host._renderer.getSeriesStyles === 'function'
      ? (this._host._renderer.getSeriesStyles(instanceId) ?? [])
      : [];
    if (metas.length > 0) {
      // ISSUE-110 🔴-1: 実系列に存在しない stale キーを剪定する（実系列集合が空のときは判定
      //   不能のため剪定しない＝従来の防御を維持）。
      this._host._commitState(reconcileSeriesStyles(this._host._state, instanceId, metas.map((m) => m.name)));
    }
    const reconciled = this._host._state.applied.find((i) => i.instanceId === instanceId);
    const styles = (reconciled && reconciled.styles) || null;
    const theme = this._activeColorTheme();
    if (!styles && !theme && metas.length === 0) {
      return; // 適用すべき上書きもテーマも無く、解決の材料も無い＝従来どおり no-op。
    }

    const def = this._host._meta.get(instanceId)?.def ?? this._host._catalog.get(inst.indicatorId);
    const params = this._host._paramsObject(reconciled ? reconciled.params : inst.params);
    const timeframe = resolveInstanceTimeframe(params, this._host._timeframe);
    const roleIndex = buildColorRoleIndex({ def, params, expandPattern: expandSeriesNamePattern });

    // 1. 実描画系列: 解決色（色のみ）＋ 保存済み patch の色以外。
    const applied = new Set();
    for (const meta of metas) {
      const name = meta.name;
      applied.add(name);
      const patch = (styles && styles[name]) || null;
      // defaultColor: null＝解決順ステップ 5 へ落ちたら色を書かない。ロック色・意味色・個別色・
      //   payload 色のどれも無い系列は「色を決める材料が無い」のであって既定色ではない。
      //   ここで既定色を書くと、payload が色を持たない系列（lwc 既定色で描かれている）の色を
      //   捏造して変えてしまう。色以外の patch は従来どおり適用する。
      const color = resolveSeriesColor({
        styles,
        seriesName: name,
        role: roleIndex.get(name) ?? null,
        theme,
        timeframe,
        payloadColor: meta.baseColor,
        defaultColor: null,
      });
      const next = { ...(patch ?? {}) };
      if (color != null) {
        next.color = color;
      }
      if (patch == null && color == null) {
        continue; // 書くべきものが何も無い。
      }
      this._host._renderer.applySeriesStyle(instanceId, name, next);
    }
    // 2. renderer が知らない系列に保存された patch は従来どおり逐語で適用する。
    //    baseColor が取れない＝解決順ステップ 4 の入力が存在しないため、色を捏造しない。
    for (const [name, patch] of Object.entries(styles ?? {})) {
      if (!applied.has(name)) {
        this._host._renderer.applySeriesStyle(instanceId, name, patch);
      }
    }
    // 3. 水準線（horizontal_line）は applySeriesStyle に到達しない（E-10）ため専用入口へ渡す。
    //    R-3: テーマが level を宣言していないときは null＝現行経路（pane は schemeColor /
    //    overlay は backend 色）のまま。
    this._applyLevelLineColor(instanceId, theme, timeframe);
  }

  // 選択中テーマの供給ポートを後から結ぶ（基本設計_指標カラーテーマ.md §7.3 ISP・DIP）。
  //   constructor 引数 `colorThemeProvider` と同じ席へ書くだけで、挙動は完全に同一（加法）。
  //   setter を持つ理由: `new IndicatorController(...)` は両 composition_root_front.js が各々
  //   書いており、constructor 引数のままだと同一 1 行を 2 か所へ手書き複製することになる
  //   （ISSUE-278 #4 で潰した複製の再生産）。setter なら共有配線（chart_app_wiring.js）の
  //   1 箇所だけで両モードを結線できる。
  //   非関数（null 含む）を渡すと「テーマなし」＝恒等へ戻る（_activeColorTheme と同じ規約）。
  setColorThemeProvider(provider) {
    this._colorThemeProvider = typeof provider === 'function' ? provider : null;
  }

  // 選択中のテーマ（未注入・供給不能は null＝恒等）。例外を外へ出さない（F-C4 の縮退と同旨）。
  _activeColorTheme() {
    if (!this._colorThemeProvider) {
      return null;
    }
    try {
      return this._colorThemeProvider() ?? null;
    } catch {
      return null;
    }
  }

  // 水準線ポートへ level トークンの解決色を渡す（未宣言・ポート非対応は no-op）。
  _applyLevelLineColor(instanceId, theme, timeframe) {
    if (typeof this._host._renderer.applyLevelLineColor !== 'function') {
      return;
    }
    // 宣言の有無を**ここで判定しない**（判定源を 2 つ作らない）。resolver の解決順に委ね、
    //   材料（ロック色・意味色・個別色・payload 色）がどれも無ければ null を返させる（R-7）。
    //   ここに `roleColors.level == null` のような別の判定を置くと、resolver 側の判定
    //   （isHex6）とずれた瞬間に既定色 #2962ff が書き込まれ、全水準線が青一色になる
    //   （schemeColor を潰す＝N-5 の破壊的変更）。水準線は styles も payload も持たないため、
    //   本呼び出しでは「意味色が決まったか否か」だけが結果を分ける。
    const color = resolveSeriesColor({
      styles: null,
      seriesName: null,
      role: ColorRole.LEVEL,
      theme,
      timeframe,
      payloadColor: null,
      defaultColor: null,
    });
    this._host._renderer.applyLevelLineColor(instanceId, color);
  }
}
