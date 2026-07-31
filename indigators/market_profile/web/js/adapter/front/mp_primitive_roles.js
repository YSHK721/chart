// mp_primitive_roles.js — 単一 MarketProfileHistogramPrimitive 上のロール別ファサード（ISP）。
//
// 設計入力（ISSUE-099 🟡-5）: MarketProfileHistogramPrimitive は 2 つの無関係なクライアント役割を
//   1 クラス（god interface）へ束ねていた。MarketProfileActor は
//   {setProfile,setVisible,setSnapshot,setSessions,setCursorTime} のみ・TfPeriodProfileActor /
//   Tooltip は {setTfPeriods,tfPeriodLevelAt} のみを排他的に使う（実測・排他サブセット）。
//   本モジュールは同一 primitive の上にロール別ファサードを定義し、各アクターへ必要面のみ注入する。
//
// 不変条件: 単一 ISeriesPrimitive の attach 点は 1 つのまま（両ファサードは同一 primitive を包む）。
//   ProfileSink は attach 用に下層 primitive を `seriesPrimitive()` で取り出せる（MarketProfileActor
//   の _attachTarget が使用）。ファサードは全メソッドを下層へ透過委譲する（挙動不変）。

// プロファイル表示役（MarketProfileActor 用）。
export class ProfileSink {
  constructor(primitive) {
    this._primitive = primitive;
  }

  setProfile(profile) { return this._primitive.setProfile(profile); }

  setVisible(visible) { return this._primitive.setVisible(visible); }

  setSnapshot(on) { return this._primitive.setSnapshot(on); }

  setSessions(sessions) { return this._primitive.setSessions(sessions); }

  setCursorTime(time) { return this._primitive.setCursorTime(time); }

  // 単一 attach 点: 下層 ISeriesPrimitive を返す（mainSeries.attachPrimitive の対象）。
  seriesPrimitive() { return this._primitive; }
}

// tf-period 列役（TfPeriodProfileActor / TfPeriodTooltip 用）。
export class TfPeriodSink {
  constructor(primitive) {
    this._primitive = primitive;
  }

  setTfPeriods(columns, unit) { return this._primitive.setTfPeriods(columns, unit); }

  // tf-period 列を束ねる価格幅（barw）。null で最小価格単位のまま（ISSUE-054）。
  setTfBinWidth(width) { return this._primitive.setTfBinWidth(width); }

  tfPeriodLevelAt(time, price) { return this._primitive.tfPeriodLevelAt(time, price); }
}
