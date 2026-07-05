// temporal_cursor.js — TemporalCursor（domain 値・因果カーソル）。
//
// 設計入力: Model A 統一成長モデル Phase 0。表示モード（normal/replay/sessions）× 成長状態（growing/static）を
//   直交化する統一モデルにおいて、因果性（未来リーク禁止）を単一定義する domain 値。
//   現状 replay.js/actor に散在する「now は必ず secs[i]・sec<=now」という畳み込み可否ルールを domain へ昇格する。
//
// 不変条件: asOf は「as-seen-at-t（この時点で観測済みの最新秒）」。canFold(sec) は sec が asOf 以下（＝観測済み）
//   のときだけ true を返す。asOf===null は「最新＝全期間（上限なし）」を表し、全 sec を畳み込み可とする。
//   domain 値＝副作用なし・自内 import のみ（外側レイヤーを参照しない・Dependency Rule）。

export class TemporalCursor {
  // asOf: 観測基準秒（UNIX 秒）。null は「最新＝全期間（上限なし）」を表す。
  constructor(asOf = null) {
    this._asOf = asOf;
  }

  // 観測基準秒。null は最新（全期間）。
  get asOf() {
    return this._asOf;
  }

  // sec を畳み込んで良いか（因果ルール）。sec <= asOf のときだけ true（未来リーク禁止）。
  //   asOf===null（最新＝全期間）は上限なし＝常に true。
  canFold(sec) {
    if (this._asOf == null) {
      return true;
    }
    return sec <= this._asOf;
  }
}
