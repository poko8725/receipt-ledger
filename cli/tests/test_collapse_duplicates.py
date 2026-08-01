"""同じ取引が複数の通知で届いたぶんを寄せる。

    cd cli && python3 -m unittest discover tests

ここで見ているのは1つだけ:

    **寄せすぎていないか。**

「ご注文確認」と「発送済み」を寄せるのは易しい。難しいのは、
**同額の買い物を本当に何度もした人を潰さない**ことのほうで、
潰しても例外は出ず、合計が小さくなるだけなので気づけない。

最初に書いた実装は (請求元・通貨・金額・日付窓) だけで寄せていて、
既存のテストにあった PayPal 6通(各 ¥630・同日)を1件に潰した。
実データでも同じ日に同じ相手から ¥610 の領収書が3通来ていて、
**課金の単位が決まっている相手ほどこの形になる**。
だから件名の条件を足した。ここのテストはその歯止めが効いているかを見る。
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from receipt_ledger.rules import collapse_duplicates  # noqa: E402

RECEIPT = "「ご利用ありがとうございます」様への支払いの領収書"


@dataclass
class Rec:
    """照合に要る列だけの Record 代用。"""
    date: str
    merchant: str
    amount: Decimal
    currency: str = "JPY"
    subject: str = ""
    billed_by: str = ""


def rec(date: str, merchant: str, amount: int, subject: str, currency: str = "JPY") -> Rec:
    return Rec(date, merchant, Decimal(amount), currency, subject)


class CollapsesSameTransaction(unittest.TestCase):
    def test_注文済みと発送済みは同じ取引(self):
        # 実データで出た形。同じ日、同じ相手、同じ金額、件名だけ違う。
        records = [
            rec("2026-07-16", "Amazon", 2520, "注文済み:「コンタクトレンズ」とその他1"),
            rec("2026-07-16", "Amazon", 2520, "発送済み:「コンタクトレンズ」とその他1"),
        ]
        kept, dropped = collapse_duplicates(records)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)

    def test_数日ずれても窓の内側なら寄せる(self):
        records = [
            rec("2026-07-16", "Amazon", 2520, "ご注文確認"),
            rec("2026-07-18", "Amazon", 2520, "商品お届けのお知らせ"),
        ]
        self.assertEqual(len(collapse_duplicates(records, window_days=3)[0]), 1)

    def test_残すのは先に来たほう(self):
        # 注文が先で発送が後なので、取引の日付としては注文日のほうが実態に近い。
        records = [
            rec("2026-07-18", "Amazon", 2520, "発送済み"),
            rec("2026-07-16", "Amazon", 2520, "注文済み"),
        ]
        kept, dropped = collapse_duplicates(records)
        self.assertEqual(kept[0].subject, "注文済み")
        self.assertEqual(dropped[0][0].subject, "発送済み")
        self.assertEqual(dropped[0][1].subject, "注文済み")


class DoesNotOverCollapse(unittest.TestCase):
    """ここが本番。潰しすぎても静かなので、条件ごとに確かめる。"""

    def test_同じ件名が並ぶのは別々の取引(self):
        # 1件ごとに1通出す定型の相手。実データの ¥610 ×3 がこの形。
        records = [rec(f"2026-07-16", "COGNOSPHERE", 610, RECEIPT) for _ in range(3)]
        self.assertEqual(len(collapse_duplicates(records)[0]), 3)

    def test_進み具合が読めない件名は寄せない(self):
        # 件名は違うが、どちらも段階を名乗っていない。推測で寄せない。
        records = [
            rec("2026-07-16", "Example", 630, "領収書 #1001"),
            rec("2026-07-16", "Example", 630, "領収書 #1002"),
        ]
        self.assertEqual(len(collapse_duplicates(records)[0]), 2)

    def test_窓の外なら別の取引(self):
        records = [
            rec("2026-07-16", "Amazon", 2520, "ご注文確認"),
            rec("2026-07-25", "Amazon", 2520, "発送済み"),
        ]
        self.assertEqual(len(collapse_duplicates(records, window_days=3)[0]), 2)

    def test_金額が違えば別の取引(self):
        records = [
            rec("2026-07-16", "Amazon", 2520, "ご注文確認"),
            rec("2026-07-16", "Amazon", 2530, "発送済み"),
        ]
        self.assertEqual(len(collapse_duplicates(records)[0]), 2)

    def test_相手が違えば別の取引(self):
        records = [
            rec("2026-07-16", "Amazon", 2520, "ご注文確認"),
            rec("2026-07-16", "楽天市場", 2520, "発送済み"),
        ]
        self.assertEqual(len(collapse_duplicates(records)[0]), 2)

    def test_通貨が違えば別の取引(self):
        # ¥2,520 と $2,520 は同じ数字だが同じ取引ではない。
        records = [
            rec("2026-07-16", "Example", 2520, "ご注文確認", currency="JPY"),
            rec("2026-07-16", "Example", 2520, "発送済み", currency="USD"),
        ]
        self.assertEqual(len(collapse_duplicates(records)[0]), 2)

    def test_日付不明は寄せない(self):
        # いつの取引か分からないものを「同じ」とは言えない。
        records = [
            rec("2026-07-16", "Amazon", 2520, "ご注文確認"),
            rec("不明", "Amazon", 2520, "発送済み"),
        ]
        self.assertEqual(len(collapse_duplicates(records)[0]), 2)

    def test_三件並んでも寄せるのは窓の内側だけ(self):
        # 16日と17日は寄る。21日は 17 日からも 16 日からも窓の外なので残る。
        records = [
            rec("2026-07-16", "Amazon", 610, "ご注文確認"),
            rec("2026-07-17", "Amazon", 610, "発送済み"),
            rec("2026-07-21", "Amazon", 610, "お届け済み"),
        ]
        kept, dropped = collapse_duplicates(records, window_days=3)
        self.assertEqual([r.date for r in kept], ["2026-07-16", "2026-07-21"])
        self.assertEqual(len(dropped), 1)

    def test_窓をゼロにすれば同じ日だけ(self):
        records = [
            rec("2026-07-16", "Amazon", 2520, "ご注文確認"),
            rec("2026-07-17", "Amazon", 2520, "発送済み"),
        ]
        self.assertEqual(len(collapse_duplicates(records, window_days=0)[0]), 2)


class KnownLimit(unittest.TestCase):
    """直せていない範囲を、直したつもりにならないよう固定しておく。"""

    def test_同じ日に同額を別々に注文すると寄ってしまう(self):
        # 件名は違い、どちらにも「注文」が入るので、条件を全部通る。
        # 件名から段階と品目の対応までは読めないので、ここは寄せてしまう。
        # だから消さずに返して、呼び出し側に必ず見せさせている。
        records = [
            rec("2026-07-16", "Amazon", 2520, "ご注文確認:「コーヒー」"),
            rec("2026-07-16", "Amazon", 2520, "ご注文確認:「紅茶」とその他1"),
        ]
        kept, dropped = collapse_duplicates(records)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)  # 見せる先があることだけは保証する


class KeyIsSelectable(unittest.TestCase):
    def test_請求元の取り方を呼び出し側が決められる(self):
        # 経費用途では、作品名で上書きする前の billed_by で揃える。
        records = [
            Rec("2026-07-16", "原神", Decimal(610), "JPY", "ご注文確認", "COGNOSPHERE"),
            Rec("2026-07-16", "崩壊：スターレイル", Decimal(610), "JPY", "発送済み", "COGNOSPHERE"),
        ]
        self.assertEqual(len(collapse_duplicates(records)[0]), 2)
        self.assertEqual(
            len(collapse_duplicates(records, key=lambda r: r.billed_by)[0]), 1
        )


if __name__ == "__main__":
    unittest.main()
