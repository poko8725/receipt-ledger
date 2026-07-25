"""PayPal の支払い領収書。

決済代行なので、送信元(service-jp@paypal.com)は請求元ではない。
本文の「マーチャント」欄に実際の請求先が入っている。

本文の該当部分:

    取引ID XXXXXXXXXXXXXXXXX
    取引日 20XX/XX/XX
    マーチャント COGNOSPHERE PTE. LTD...
    請求書ID XXXXXXXXXXXXXXXXXXX
    説明 単価 数量 金額
    天空紀行 ¥1,220 JPY 1 ¥1,220 JPY
    小計 ¥1,220 JPY
    合計 ¥1,220 JPY

マーチャント名は PayPal 側で切り詰められて届くことがあり
(「COGNOSPHERE PTE. LTD...」)、同じ相手が複数の表記で来る。
正規化は rules.normalize_merchant に任せる。
"""

from __future__ import annotations

import re

from ..rules import normalize_merchant
from .base import ParsedReceipt

MERCHANT = re.compile(r"マーチャント\s*(.+?)\s*(?:\n|請求書ID|取引ID|説明)")

# 「説明 単価 数量 金額」の直後が最初の明細行。
# 金額表記の手前までを品目名とみなす。
ITEM = re.compile(r"説明\s*単価\s*数量\s*金額\s*(.+?)\s*[¥￥$€£]")


class PayPalParser:
    name = "paypal"

    def matches(self, sender: str, subject: str, body: str) -> bool:
        return "paypal" in sender.lower() or "マーチャント" in body and "PayPal" in body

    def parse(self, sender: str, subject: str, body: str) -> ParsedReceipt | None:
        m = MERCHANT.search(body)
        if not m:
            return None
        merchant = normalize_merchant(m.group(1))
        if not merchant:
            return None
        item = ""
        i = ITEM.search(body)
        if i:
            item = " ".join(i.group(1).split())[:60]
        return ParsedReceipt(merchant=merchant, item=item, source_label="PayPal")
