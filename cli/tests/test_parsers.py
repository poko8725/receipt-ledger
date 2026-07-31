"""領収書パーサーが、送信元では分からない請求元を取り出せること。

    cd cli && python3 -m unittest discover tests

parsers/ が存在する理由は1つで、**決済代行を挟むと送信元が請求元と
一致しない**こと。PayPal 経由の課金は全部 paypal.com から届くので、
送信元だけ見ていると全件「PayPal」に潰れて内訳が消える。

だからここで見るのは「解析できたか」ではなく、
**送信元のドメインが請求元に化けていないか**。
潰れても例外は出ず、件数も金額も合ったまま内訳だけ失われるので、
テストが無ければ気づけない。

本文は各パーサーの docstring にある実物の形をなぞってある。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from receipt_ledger.parsers import PARSERS, parse_receipt  # noqa: E402


PAYPAL = ("service-jp@paypal.com", "PayPal のご利用明細", """取引ID 1AB23456CD789012E
取引日 2026/07/15
マーチャント COGNOSPHERE PTE. LTD...
請求書ID 987654321
説明 単価 数量 金額
天空紀行 ¥1,220 JPY 1 ¥1,220 JPY
小計 ¥1,220 JPY
合計 ¥1,220 JPY
PayPal
""")

GOOGLE_PLAY = ("googleplay-noreply@google.com", "ご注文明細", """\
Google Play での miHoYo Inc. からの購入が完了しました。
注文番号: GPA.1234-5678-9012-34567
アイテム 価格
280水晶（月パス） (崩壊3rd) ￥490
合計: ￥490
""")

APPLE_NEW = ("no_reply@email.apple.com", "ご購入の領収書", """Apple 領収書
App Store
ドラゴンクエストウォーク 歩く楽しみが増える位置情報ゲーム
ジェムパックC
アプリ内課金
¥1,500
""")

APPLE_ITEM_ONLY = ("no_reply@email.apple.com", "ご購入の領収書", """Apple 領収書
--------------------------------------------------
聖晶石 168個 ¥10,000
""")

XSOLLA = ("mailer@xsolla.com", "購入完了", """購入情報
製品
鳴潮
企業
Xsolla (USA), Inc.
購入: ¥610
Lunite Subscription ¥610
小計 ¥610
合計 ¥610
""")

PLAYSTATION = ("reply@txn-email.playstation.com", "ご購入明細", """購入日: 2026/07/15
詳細 価格
PlayStation Plusエッセンシャル : 1ヶ月利用権 (定額サービス)
定額サービスの加入料: ¥850 が次の日付で請求されます: 2026/08/15
小計: ¥850
合計: ¥850
""")

# 決済代行を挟むもの。送信元の名前が請求元になっていたら潰れている。
PROXIES = [
    ("PayPal", PAYPAL, "paypal"),
    ("Google Play", GOOGLE_PLAY, "google"),
    ("Xsolla", XSOLLA, "xsolla"),
]


class ProxyDoesNotSwallowTheMerchant(unittest.TestCase):
    """**この1本が parsers/ の存在理由そのもの。**

    決済代行の名前が請求元の欄に出てきたら、内訳が消えている。
    """

    def test_the_payment_proxy_never_becomes_the_merchant(self):
        for label, (sender, subject, body), forbidden in PROXIES:
            with self.subTest(proxy=label):
                result = parse_receipt(sender, subject, body)
                self.assertIsNotNone(result, f"{label} を解析できていない")
                self.assertNotIn(
                    forbidden, result.merchant.lower(),
                    f"請求元が決済代行({label})に潰れている: {result.merchant!r}",
                )


class EachParser(unittest.TestCase):
    """各フォーマットから何を取り出すか。"""

    def parsed(self, case):
        """解析できなかった場合を、属性エラーではなく失敗として出す。

        None のまま .merchant を見ると AttributeError になり、
        「解析できなかった」のか「値が違う」のかが読み取れない。
        """
        result = parse_receipt(*case)
        self.assertIsNotNone(result, "解析できていない（送信元推測に落ちている）")
        return result

    def test_paypal_reads_the_merchant_field(self):
        r = self.parsed(PAYPAL)
        self.assertEqual(r.merchant, "COGNOSPHERE")
        self.assertEqual(r.item, "天空紀行")
        self.assertEqual(r.source_label, "PayPal")

    def test_google_play_prefers_the_app_over_the_developer(self):
        """開発元(miHoYo Inc.)ではなくアプリ名(崩壊3rd)でまとめる。

        同じ開発元が複数タイトルを出すので、開発元でまとめると
        「どのゲームに使ったか」が分からなくなる。
        """
        r = self.parsed(GOOGLE_PLAY)
        self.assertEqual(r.merchant, "崩壊3rd")
        self.assertNotIn("miHoYo", r.merchant)

    def test_apple_new_layout_takes_app_then_item(self):
        r = self.parsed(APPLE_NEW)
        self.assertTrue(r.merchant.startswith("ドラゴンクエストウォーク"))
        self.assertEqual(r.item, "ジェムパックC")

    def test_apple_falls_back_to_apple_when_only_an_item_is_present(self):
        """アプリ名の行が無い領収書では、品目をアプリ名と取り違えない。

        「聖晶石 168個」は課金アイテムであってアプリ名ではないので、
        請求元は Apple のままにして品目だけ残す。
        """
        r = self.parsed(APPLE_ITEM_ONLY)
        self.assertEqual(r.merchant, "Apple")
        self.assertIn("聖晶石", r.item)

    def test_xsolla_reads_the_product_not_the_company(self):
        """「企業」欄は Xsolla なので、そちらを拾ってはいけない。"""
        r = self.parsed(XSOLLA)
        self.assertEqual(r.merchant, "鳴潮")
        self.assertEqual(r.item, "Lunite Subscription")

    def test_playstation_splits_title_from_the_breakdown(self):
        r = self.parsed(PLAYSTATION)
        self.assertEqual(r.merchant, "PlayStation Plusエッセンシャル")
        self.assertIn("1ヶ月利用権", r.item)


class Registry(unittest.TestCase):
    """レジストリの振る舞い。"""

    def test_all_parsers_are_registered(self):
        # 名前で自動収集していないので、足したのに登録し忘れると
        # そのフォーマットだけ静かに送信元推測へ落ちる。
        names = {p.name for p in PARSERS}
        self.assertEqual(
            names,
            {"paypal", "google-play", "apple", "xsolla", "playstation"},
        )

    def test_unknown_format_returns_none(self):
        """当たらなければ None。呼び出し側が送信元推測に落ちるため。"""
        self.assertIsNone(
            parse_receipt("shop@example.invalid", "ご購入", "ご利用ありがとうございます 合計 ¥100")
        )

    def test_one_broken_parser_does_not_stop_the_others(self):
        """1つのフォーマットの読み違いで全体を止めない。

        parse_receipt は例外を握り潰して次へ進む設計なので、
        その握り潰しが効いていることを見る。
        """
        class Exploding:
            name = "exploding"

            def matches(self, sender, subject, body):
                raise RuntimeError("読み違えた")

            def parse(self, sender, subject, body):  # pragma: no cover
                raise AssertionError("ここには来ない")

        PARSERS.insert(0, Exploding())
        try:
            r = parse_receipt(*XSOLLA)
            self.assertIsNotNone(r, "先頭のパーサーが落ちて全体が止まっている")
            self.assertEqual(r.merchant, "鳴潮")
        finally:
            PARSERS.pop(0)


if __name__ == "__main__":
    unittest.main()
