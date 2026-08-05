"""ラベル(合計/Total)と金額の間に、レイアウト由来の空白がどれだけ入っても拾えること。

    cd cli && python3 -m unittest discover tests

AMOUNT_PATTERNS は最初からラベル付きを最優先にする設計だった。
だが読み飛ばしの上限 `[^\\d¥￥$€£]{0,12}` が**空白も数えていた**ため、
HTML のテーブルを平文にして空白が数十文字並ぶと上限を超え、
ラベル付きが不成立になって裸の「¥650」(＝明細の1件目)を拾っていた。

実データの Apple の領収書は1通に複数のアプリ課金が入る。

    問題を報告する → ¥650
    問題を報告する → ¥1,200
    問題を報告する → ¥2,000
    合計          → ¥3,850     ← 650+1,200+2,000

**例外は出ない。合計が静かに小さくなる。**
2026-08-05 の実測で、手元の Apple の領収書 401 通のうち 14 通、
**合計 ¥27,660** が欠けていた。以下の期待値はその実データから取っている。

---

**このファイルを書くときに一度失敗している。**最初に書いた7件のうち、
修正前のコードで実際に落ちるのは1件だけだった。残り6件は本文に金額が1つしか無く、
ラベル付きが外れてもフォールバックの「¥1,234」パターンが同じ値を拾うので、
**欠陥があっても緑になる**テストだった。

この欠陥は「合計より前に別の金額が並んでいる」ときにしか発現しない。
だから ItemsBeforeTotal の各ケースには**必ず明細を先に置く**こと。
明細を消すと、そのテストは何も検査しなくなる。
"""

from __future__ import annotations

import time
import unittest
from decimal import Decimal

from receipt_ledger.rules import AMOUNT_PATTERNS, extract_amount

# 明細が先に並んでいる状況を作る。これが無いテストは、この欠陥を検出できない。
ITEMS = "問題を報告する\n¥650\n問題を報告する\n¥1,200\n問題を報告する\n¥2,000\n"


def amount(text: str) -> Decimal | None:
    got = extract_amount(text)
    return got[0] if got else None


class ItemsBeforeTotal(unittest.TestCase):
    """明細が先に並んでいても、ラベル付きの合計を取る。

    **欠陥を差し戻すと落ちるのは、空白が12文字を超える3件**
    (test_html_cell_layout / test_same_line_wide_padding /
    test_observed_maximum_gap)。この3件が、この欠陥を実際に検出している。

    残る2件(test_narrow_gap / test_full_width_yen_with_items)は
    空白が12文字以内なので**修正前でも通る**。役割は回帰防止で、
    「広げた結果、狭い側が壊れていないか」を見ている。混同しないこと。

    検出できているかは、rules.AMOUNT_PATTERNS[0] を旧版
    (`[^\\d¥￥$€£]{0,12}?` で空白も数える版)に差し替えて、
    上の3件が落ちることで確かめられる。
    """

    def test_html_cell_layout(self) -> None:
        # HTML のセル間が改行と空白で埋まる形。実データの並びそのまま。
        body = ITEMS + "合計 \n            \n           \n                          ¥3,850\n"
        self.assertEqual(amount(body), Decimal("3850"))

    def test_same_line_wide_padding(self) -> None:
        # 同一行だが、ラベルと金額の間が空白24文字。
        self.assertEqual(amount(ITEMS + "合計:" + " " * 24 + "¥3,850"), Decimal("3850"))

    def test_observed_maximum_gap(self) -> None:
        # 手元の1500通で測ったラベル→金額の空白は中央値25・最大53だった。
        # 上限はここから決めている。53が通ることを固定しておく。
        self.assertEqual(amount(ITEMS + "合計" + " " * 53 + "¥3,850"), Decimal("3850"))

    def test_narrow_gap(self) -> None:
        # 空白1文字。修正前から通っていた形だが、明細を先に置いた状態で
        # 壊れていないことを見る。
        self.assertEqual(amount(ITEMS + "合計\n¥3,850"), Decimal("3850"))

    def test_full_width_yen_with_items(self) -> None:
        body = ITEMS + "合計金額  \n        ￥11,092 JPY"
        self.assertEqual(amount(body), Decimal("11092"))


class ExistingBehaviourUnchanged(unittest.TestCase):
    """空白の扱いを変えただけで、通貨の判定や後置表記は動かさない。

    **ここは回帰防止であって、今回の欠陥は検出しない。**
    修正前のコードでも通る。役割を混ぜないよう分けてある。
    """

    def test_narrow_gap_single_amount(self) -> None:
        self.assertEqual(amount("合計 ¥1,980"), Decimal("1980"))

    def test_currency_is_not_eaten(self) -> None:
        # 読み飛ばしが貪欲だと USD を食い潰して既定の円に化ける。
        got = extract_amount("Total: USD 49.99")
        assert got is not None
        self.assertEqual(got[0], Decimal("49.99"))
        self.assertEqual(got[1], "USD")

    def test_suffix_currency(self) -> None:
        self.assertEqual(amount("お支払い金額 3,980円"), Decimal("3980"))


class LabelMustNotReachTooFar(unittest.TestCase):
    """上限そのものは残す。ラベルが遠くの無関係な数字に当たってはいけない。

    空白の許容を広げた分、この方向の誤り(ラベルが別の行の数字を拾う)は
    起きやすくなっている。**広げた側だけでなく、こちらも固定しておく。**
    """

    def test_reach_is_bounded(self) -> None:
        # 空白の実行は2箇所(任意グループ内・数字直前)なので届く距離は 64+8=72。
        # 実データの最大は53。二分探索で境界を確認して固定してある。
        self.assertIsNotNone(AMOUNT_PATTERNS[0].search("合計" + " " * 72 + "7000"))
        self.assertIsNone(AMOUNT_PATTERNS[0].search("合計" + " " * 73 + "7000"))

    def test_non_space_gap_still_capped(self) -> None:
        self.assertIsNone(AMOUNT_PATTERNS[0].search("合計" + "あ" * 13 + "7000"))

    def test_label_does_not_jump_to_next_section(self) -> None:
        # 合計のあとに別の見出しと数字が続く形。非空白12文字の上限が効いて、
        # 「ポイント 1,234」をこの合計の値として拾ってはいけない。
        body = "合計\n\n\n次回のお買い物でつかえるポイントのご案内\n1,234\n"
        m = AMOUNT_PATTERNS[0].search(body)
        self.assertIsNone(m, f"ラベルが別の節の数字に届いている: {m.group(0)!r}" if m else "")


class NoRedos(unittest.TestCase):
    """メールは第三者が送れるので、1通あたりの上限時間を小さく保つ。

    空白の実行を3箇所・上限400で書いたときは、空白6万字+非空白6万字で
    0.826 秒かかった。空白と非空白でクラスを分け、実行を2箇所に絞ってある。
    """

    def test_adversarial_input_is_fast(self) -> None:
        for text in (
            "合計" + " " * 60000 + "x" * 60000,
            "合計" + (" x" * 30000),
            "合計" + "1" * 60000,
        ):
            started = time.monotonic()
            AMOUNT_PATTERNS[0].search(text)
            self.assertLess(time.monotonic() - started, 0.2)


if __name__ == "__main__":
    unittest.main()
