// market_profile_actor.js — Market Profile の薄い制御アクター（取得→primitive 反映・トグル状態）。
//
// 設計入力: 依頼「プロファイルを取得して primitive に反映する薄い制御・ES Modules・既存層構造」。
//   trade_markers_renderer.js と同層（adapter/front）。client（取得）と primitive（描画）を注入し、
//   本アクターは「トグル状態の保持」と「有効時の取得→反映」の編成のみを担う（SRP）。
//   依存はすべて抽象（duck-typing）に向け、composition root が具象を注入する（DIP）。
//
// 非破壊方針: primitive は初回有効化まで mainSeries へ attach しない（OFF 時はチャートに一切触れない）。
//   取得失敗（client が null）時も既存描画へ干渉せず、前回 profile を保持する。

export class MarketProfileActor {
  // client: fetchProfile(context)->profile|null。primitive: setProfile/setVisible。
  // mainSeries: attachPrimitive（v5・非提供時は attach を skip＝後方互換）。
  // getContext: ()->{datasetRef,timeframe,limit,...}（取得時点の現在チャート状態を遅延読み取り）。
  constructor({ client, primitive, mainSeries, getContext } = {}) {
    this._client = client;
    this._primitive = primitive;
    this._mainSeries = mainSeries;
    this._getContext = typeof getContext === 'function' ? getContext : () => ({});
    this._enabled = false;
    this._attached = false;
  }

  isEnabled() {
    return this._enabled;
  }

  // トグル。ON: 初回のみ attach → 取得して反映 → 表示。OFF: 非表示（取得しない）。
  async setEnabled(enabled) {
    this._enabled = !!enabled;
    if (this._enabled) {
      this._ensureAttached();
      await this.refresh();
      this._primitive.setVisible(true);
    } else {
      this._primitive.setVisible(false);
    }
  }

  // 現在のコンテキストで再取得し反映する（有効時のみ）。null は反映しない（前回描画保持）。
  async refresh() {
    if (!this._enabled) {
      return;
    }
    const profile = await this._client.fetchProfile(this._getContext());
    if (profile) {
      this._primitive.setProfile(profile);
    }
  }

  // primitive を mainSeries へ一度だけ attach する（attachPrimitive 非提供時は skip）。
  _ensureAttached() {
    if (this._attached) {
      return;
    }
    if (this._mainSeries && typeof this._mainSeries.attachPrimitive === 'function') {
      this._mainSeries.attachPrimitive(this._primitive);
      this._attached = true;
    }
  }
}
