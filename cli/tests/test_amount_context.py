"""金額の形をしているが、支払った額ではない数字。

    cd cli && python3 -m unittest discover tests

ここで見ているのは1つだけ:

    **拾った数字が「払った額」なのか、ただ金額の形をしているだけなのか。**

広告メールの「3,000円相当」も、脚注の「3,980円以上で送料無料」も、
値上げ予告の「新料金：¥2,500」も、正規表現には金額として見える。
落ちても例外は出ず、合計が静かに増えるだけなので、テストが無ければ気づけない。

実データ(2026年7月後半)で誤検出した4件をそのまま入力にしてある。
このときの誤検出だけで ¥12,030 あった。
"""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from receipt_ledger.rules import extract_amount  # noqa: E402


class NotPaidAmount(unittest.TestCase):
    """支払いではない数字は拾わない。"""

    def test_当選賞品の価値は支払いではない(self):
        text = "抽選でプレゼントが当たる 500名さまへ Amazonギフトカード（※）3,000円相当"
        self.assertIsNone(extract_amount(text))

    def test_送料無料のしきい値は支払いではない(self):
        text = "※1　3,980円(税込)以上ご購入で送料無料となります。"
        self.assertIsNone(extract_amount(text))

    def test_値上げ予告の新旧料金はどちらも支払いではない(self):
        text = "新料金：¥2,500 — 旧料金：¥1,980"
        self.assertIsNone(extract_amount(text))

    def test_ゲーム内通貨の個数は金額ではない(self):
        # 「合計」というラベルが付いているぶん、ラベル優先の規則に一番強く当たる。
        text = "バージョンイベントに参加すると、合計で星玉×2,550を獲得できます"
        self.assertIsNone(extract_amount(text))

    def test_レートは支払いではない(self):
        # 広告の「◯円につき1マイル」。金額の形をしていて、件名に「支払」も入る。
        text = "ANAマイレージモール経由でのお買い物で200円につき1マイルが貯まる！"
        self.assertIsNone(extract_amount(text))

    def test_ラベル付きでも回数は金額ではない(self):
        # 「合計」が付くのでラベル優先の規則に強く当たるが、単位は回。
        text = "期間中、無料で「十の導き」を5回、合計50回分の「導き」を行えます。"
        self.assertIsNone(extract_amount(text))

    def test_期間あたりの料率は支払いではない(self):
        # カート放棄を促す広告に出る形。件名に "Purchase" が入るので
        # 「取引を示す語」の判定は通ってしまい、ここでしか落とせない。
        self.assertIsNone(extract_amount("EXAMPLE 365 BASIC USD 19.99/year"))
        self.assertIsNone(extract_amount("Example 365 Basic USD 1.99/month"))

    def test_値引き前の定価は支払いではない(self):
        # 「総額116,570円の品が 80,000 円」。ラベル付きで、通貨の後置を挟む。
        text = "生豆8種類が合計4kg付いてお得！ 総額116,570円の品が 80,000 円"
        got = extract_amount(text)
        self.assertNotEqual(got and got[0], Decimal("116570"))


class RealAmountSurvives(unittest.TestCase):
    """本物は落とさない。弾く側を強くしすぎると、静かに欠測する。"""

    def test_しきい値の後ろにある本物を拾う(self):
        # 最初の一致で打ち切る実装だと、脚注の 3,980 を返して本物に届かない。
        text = "1,000円以上のご購入で送料無料\n合計 ¥1,220"
        self.assertEqual(extract_amount(text), (Decimal("1220"), "JPY"))

    def test_括弧書きを挟んでも本物は本物(self):
        self.assertEqual(extract_amount("合計 ¥1,220 (税込)"), (Decimal("1220"), "JPY"))

    def test_ポイント進呈が後ろに続いても本物は本物(self):
        # 「ポイント」は弾く語だが、括弧の中まで見に行くと本物を落とす。
        text = "合計 ¥1,220 (10ポイント進呈)"
        self.assertEqual(extract_amount(text), (Decimal("1220"), "JPY"))

    def test_通貨付きの本物(self):
        self.assertEqual(extract_amount("Total: USD 49.99"), (Decimal("49.99"), "USD"))


if __name__ == "__main__":
    unittest.main()
