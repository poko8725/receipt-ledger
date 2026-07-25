"""領収書パーサーのレジストリ。

上から順に matches() を試し、最初に当たったものが読む。
1つも当たらなければ None を返し、呼び出し側は送信元からの推測に落ちる。

新しいフォーマットを足す手順:
  1. parsers/ に1ファイル追加し、base.ReceiptParser を満たすクラスを書く
  2. 下の PARSERS に登録する
金額抽出・集計・出力は触らない。

追加候補(実データで確認済みの分布):
  Steam           noreply@steampowered.com
  PlayStation     reply@txn-email.playstation.com
  Xsolla          mailer@xsolla.com
"""

from __future__ import annotations

from .base import ParsedReceipt, ReceiptParser
from .apple import AppleParser
from .google_play import GooglePlayParser
from .paypal import PayPalParser

PARSERS: list[ReceiptParser] = [
    PayPalParser(),
    GooglePlayParser(),
    AppleParser(),
]


def parse_receipt(sender: str, subject: str, body: str) -> ParsedReceipt | None:
    for parser in PARSERS:
        try:
            if parser.matches(sender, subject, body) :
                result = parser.parse(sender, subject, body)
                if result:
                    return result
        except Exception:
            # 1つのフォーマットの読み違いで全体を止めない
            continue
    return None


__all__ = ["PARSERS", "ParsedReceipt", "ReceiptParser", "parse_receipt"]
