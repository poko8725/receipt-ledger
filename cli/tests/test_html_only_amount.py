"""金額が text/html にしか無いメールを読めること。

    cd cli && python3 -m unittest discover tests

Steam の購入確認は text/plain が案内だけで、金額は text/html にしか無い。
`get_body_text` は text/plain があればそこで返していたので、
**同じメールの中に合計が書いてあるのに読めていなかった。**

    text/plain (3KB)   詳細を確認するには、https://store.steampowered.com/... をご覧ください
    text/html (57KB)   小計 ¥ 5,460 / 消費税@10% ¥ 546 / 合計: ¥ 6,006

`parsers/__init__.py` には「Steam は本文に明細が無く、リンク先の Web ページに
しかない」と対応不能として書いてあったが、誤りだった。中身は最初から入っていて、
欠けていたのは**こちらのパートの選び方**だけだった。

2026-08-08 の実測では、これで 2026 年ぶんの「受け取ったのに証憑になっていない」が
8 件から 4 件に減った。減った4件は Steam の購入確認
(¥6,006 / ¥8,980 / ¥1,785 / ¥2,750)で、他の送信元での誤検出は 0 だった。

---

**継ぎ足しであって差し替えではない。**text/plain で金額が読めているときに
html を混ぜると、明細が二重に並んで別の金額を拾いうる。
`PlainWins` はそれを固定する。片方だけ通しても意味が無いので、両方残すこと。
"""

from __future__ import annotations

import email
import unittest
from decimal import Decimal

from receipt_ledger.analyze import get_body_text
from receipt_ledger.rules import extract_amount

# 実物と同じ構造。text/plain はリンクの案内だけで、金額をひとつも含まない。
STEAM_PLAIN = (
    "こんにちは example_userさん\n\n"   # 実データのアカウント名は入れない（公開リポジトリ）
    "Steam でのお取引、ありがとうございました。\n"
    "詳細を確認するには、https://store.steampowered.com/email/VATPurchaseReceipt"
    "?sparams=eJx9VW1vmzAQ&check=5d832dde\nをご覧ください。\n\n"
    "今後ともSteamをよろしくお願いします。\nSteamチーム一同\n"
)

STEAM_HTML = (
    "<html><body><table>"
    "<tr><td>紅の錬金術士と白の守護者 ～レスレリアーナのアトリエ～</td></tr>"
    "<tr><td>小計 (消費税 を除く): ¥ 5,460</td></tr>"
    "<tr><td>消費税@10%： ¥ 546</td></tr>"
    "<tr><td><strong>合計: ¥ 6,006</strong></td></tr>"
    "<tr><td>インボイス： 350853430627245056</td></tr>"
    "</table></body></html>"
)


def build(plain: str, html: str | None) -> email.message.Message:
    """multipart/alternative を組む。html を None にすると text/plain だけ。

    **バイト列から組むこと。**`message_from_string` に非 ASCII を渡すと、
    `get_payload(decode=True)` が raw-unicode-escape で符号化し直すので
    日本語も `¥` も壊れる。実物はバイト列で来るので、そちらに合わせる。
    """
    parts = [f"Content-Type: text/plain; charset=utf-8\r\n\r\n{plain}"]
    if html is not None:
        parts.append(f"Content-Type: text/html; charset=utf-8\r\n\r\n{html}")
    boundary = "b0undary"
    body = f"\r\n--{boundary}\r\n".join([""] + parts) + f"\r\n--{boundary}--\r\n"
    raw = (
        'Content-Type: multipart/alternative; boundary="' + boundary + '"\r\n'
        "Subject: Steam でのご購入、ありがとうございます！\r\n"
        "From: \"Steam Support\" <noreply@steampowered.com>\r\n"
        "\r\n" + body
    )
    return email.message_from_bytes(raw.encode("utf-8"))


class HtmlOnlyAmount(unittest.TestCase):
    def test_reads_total_from_html_part(self):
        """text/plain に金額が無ければ text/html まで見る。"""
        body = get_body_text(build(STEAM_PLAIN, STEAM_HTML))
        found = extract_amount(body)
        self.assertIsNotNone(found, "html にある合計を読めていない")
        self.assertEqual(found[0], Decimal("6006"))

    def test_keeps_plain_text_too(self):
        """差し替えではなく継ぎ足し。plain 側にしか無い記述を捨てない。"""
        body = get_body_text(build(STEAM_PLAIN, STEAM_HTML))
        self.assertIn("Steamチーム一同", body)
        self.assertIn("インボイス", body)

    def test_no_html_part(self):
        """html が無いものは今までどおり。空文字や例外にしない。"""
        body = get_body_text(build(STEAM_PLAIN, None))
        self.assertIn("Steam でのお取引", body)
        self.assertIsNone(extract_amount(body))


class PlainWins(unittest.TestCase):
    """text/plain で金額が読めるときは html を触らない。

    **これが無いと、直したつもりで既存の金額を壊せる。**html には送料や
    ポイントなど plain に出ない数字が並ぶことがあり、継ぎ足すと
    ラベル付きの合計より手前にそれが来て、別の値を拾いうる。
    """

    PLAIN_WITH_TOTAL = "ご注文ありがとうございます\n合計: ¥3,850\n"
    HTML_WITH_OTHER = "<html><body>ポイント 12,000 pt<br>¥99,999</body></html>"

    def test_amount_unchanged(self):
        body = get_body_text(build(self.PLAIN_WITH_TOTAL, self.HTML_WITH_OTHER))
        self.assertEqual(extract_amount(body)[0], Decimal("3850"))

    def test_html_not_appended(self):
        body = get_body_text(build(self.PLAIN_WITH_TOTAL, self.HTML_WITH_OTHER))
        self.assertNotIn("99,999", body)


if __name__ == "__main__":
    unittest.main()
