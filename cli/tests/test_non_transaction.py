"""金額は払った額の形をしているが、そのメール自体が取引ではないもの。

    cd cli && python3 -m unittest discover tests

test_amount_context.py が見ているのは**数字の文脈**（その数字は払った額か）。
ここで見ているのはその1段上、**メールの種別**（そもそも取引の通知か）である。

数字の文脈を直しても、こちらは残っていた。広告メールに載っている製品価格は
「¥599,800」と裸で置かれていて、賞品でも送料でも値上げ予告でもないので、
文脈判定は正しく「払った額の形だ」と判定してしまう。

実データ(2024-12-16 以降 613 件)で誤検出していたものをそのまま入力にしてある。
この誤検出だけで ¥1,996,358 あり、**計上額の 53% を占めていた**。

**このテストは弾く側なので、取りこぼし（本物の領収書が落ちる）のほうが害が大きい。**
NonTransactionDropped と同じだけ TransactionSurvives を厚くしておくこと。
"""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from receipt_ledger.rules import extract_amount, non_transaction_reason  # noqa: E402


class NonTransactionDropped(unittest.TestCase):
    """取引ではないメールは理由つきで落とす。"""

    def test_広告メールの製品価格(self):
        # 実データ最大の誤検出。買っていない Vision Pro が支出に立っていた。
        reason = non_transaction_reason(
            "Apple Vision Pro。新たにM5のパワーを内蔵。10月22日発売。",
            "Apple",
            "Apple Vision Pro ¥599,800（税込）から\nApple Account ・ 購入履歴 ・ 販売条件",
            Decimal("599800"),
        )
        self.assertIsNotNone(reason)

    def test_広告のフッタにある購入履歴リンクで通してはいけない(self):
        # 件名ではなく本文全体を見ると、Apple の広告はフッタの「購入履歴」で通る。
        # 本文を見てよいのは「その金額にラベルが付いているか」だけ。
        self.assertIsNotNone(non_transaction_reason(
            "最新世代のiPhoneがついに登場。", "Apple",
            "購入履歴 販売条件 プライバシーポリシー ¥179,800", Decimal("179800")))

    def test_私信の本文にある金額(self):
        # 請求元に個人名が入り、金額だけが支出として並んでいた。
        # メールボックスの除外では防げない（受信トレイに来る）。
        self.assertIsNotNone(non_transaction_reason(
            "Re: 復職について", "山田 太郎", "…月額 66,888 円…", Decimal("66888")))

    def test_カード会社の請求額通知は明細ではなく合計(self):
        # 件名に「支払」が入るので語では落とせない。発行元で落とす。
        self.assertIsNotNone(non_transaction_reason(
            "エポスカードからのお知らせ：お支払額のご案内", "エポスカード",
            "ご請求額 ¥110", Decimal("110")))

    def test_値上げ予告は実際の課金ではない(self):
        # 実際の課金は同月に別の領収書として届く。両方数えると二重になる。
        self.assertIsNotNone(non_transaction_reason(
            "サブスクリプション料金の改定", "Apple",
            "月額¥450から月額¥540へ引き上げます", Decimal("450")))

    def test_値引き前の定価は支払いではない(self):
        # 「総額116,570円の品が 80,000 円」。ラベル付きなので領収書と同じ形に見える。
        # ここは金額の文脈の側で落とす（extract_amount が定価を返さない）。
        text = "生豆8種類が合計4kg付いてお得！ 総額116,570円の品が 80,000 円"
        got = extract_amount(text)
        self.assertNotEqual(got and got[0], Decimal("116570"))


class TransactionSurvives(unittest.TestCase):
    """本物の領収書を落とさない。**落ちても例外は出ず、合計が静かに減る。**"""

    def test_件名に領収書とあれば通る(self):
        self.assertIsNone(non_transaction_reason(
            "Apple からの領収書です", "Apple", "", Decimal("1980")))

    def test_件名が素っ気なくても本文で合計として立っていれば通る(self):
        # 件名が請求元の名前だけ、という領収書がある。件名だけで判定すると落ちる。
        self.assertIsNone(non_transaction_reason(
            "PayPal", "PayPal", "合計 ¥630 JPY", Decimal("630")))

    def test_英語の領収書(self):
        self.assertIsNone(non_transaction_reason(
            "Your receipt from Anthropic, PBC #2192-7975-1594",
            "Anthropic, PBC", "", Decimal("20")))

    def test_お届け確認も取引の通知(self):
        # 注文確認と対で届く。二重計上は collapse_duplicates の仕事であって、
        # ここで落とすと、お届け確認しか来ない相手の分がまるごと消える。
        self.assertIsNone(non_transaction_reason(
            "【送信専用】商品お届け確認★なんでも酒やカクヤス", "なんでも酒やカクヤス",
            "", Decimal("23920")))

    def test_ポイントチャージの完了通知(self):
        self.assertIsNone(non_transaction_reason(
            "DMM：ポイントチャージが完了しました", "DMM.com", "", Decimal("1000")))

    def test_ふるさと納税の申し込み受付(self):
        self.assertIsNone(non_transaction_reason(
            "【さとふる】 ふるさと納税の申し込みを承りました", "株式会社さとふる",
            "", Decimal("21000")))

    def test_カード会社でも発行元でなければ落とさない(self):
        # 「支払」を含む件名は本物の領収書にも多い。落とすのは発行元だけ。
        self.assertIsNone(non_transaction_reason(
            "COGNOSPHERE PTE. LTD. 様への支払いの領収書", "COGNOSPHERE",
            "", Decimal("1220")))


if __name__ == "__main__":
    unittest.main()
