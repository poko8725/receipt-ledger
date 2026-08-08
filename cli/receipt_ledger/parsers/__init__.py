"""領収書パーサーのレジストリ。

上から順に matches() を試し、最初に当たったものが読む。
1つも当たらなければ None を返し、呼び出し側は送信元からの推測に落ちる。

新しいフォーマットを足す手順:
  1. parsers/ に1ファイル追加し、base.ReceiptParser を満たすクラスを書く
  2. 下の PARSERS に登録する
金額抽出・集計・出力は触らない。

対応できないと確認済みのもの:
  Nintendo     残高チャージの通知で、商品名がそもそも存在しない

**「対応できない」と書いてあったものを1件取り下げた。**Steam について
「メール本文に明細が無く、リンク先の Web ページにしかない」と書いていたが、
2026-08-08 に実物を開いて誤りと分かった。案内だけなのは text/plain のほうで、
同じメールの text/html には品目・税額・合計・インボイス番号まで入っている。
**読めなかったのは相手の形式ではなく、こちらが片方のパートしか見ていなかったから**
(`analyze.get_body_text` を直した)。ここに専用のパーサーは要らない。

対応不能と書く前に、全パートを平文にして中身を確かめること。
"""

from __future__ import annotations

from .base import ParsedReceipt, ReceiptParser
from .apple import AppleParser
from .google_play import GooglePlayParser
from .paypal import PayPalParser
from .playstation import PlayStationParser
from .xsolla import XsollaParser

PARSERS: list[ReceiptParser] = [
    PayPalParser(),
    GooglePlayParser(),
    AppleParser(),
    XsollaParser(),
    PlayStationParser(),
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
