"""入力（HTTP の JSON）を Input Model へ、Output Model を JSON へ翻訳する層。

controller は**数えない・計算しない**。分位の算出・並び替え・到達判定は usecase / domain が
持ち、ここは形の変換と失敗の翻訳だけを行う（フロントも同じ理由で再計算しない＝単一ソース）。
"""
