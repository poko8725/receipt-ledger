"""Xsolla の購入領収書。

ゲーム内課金の決済代行なので、送信元(mailer@xsolla.com)は請求元ではない。
本文の「製品」欄にタイトルが直接入っているため、決済代行のなかでは
最も素直に請求元が取れる。

本文の該当部分:

    購入情報
    製品
    鳴潮
    企業
    Xsolla (USA), Inc.
    ...
    購入:
    ¥610
    Lunite Subscription
    小計 ¥610
    合計 ¥610
"""

from __future__ import annotations

import re

from .base import ParsedReceipt

PRODUCT = re.compile(r"製品\s+(.{1,60}?)\s+企業")
# 「購入:」の直後は金額で、その次が品目名
ITEM = re.compile(r"購入[:：]\s*[¥￥]\s*[\d,]+\s+(.{1,60}?)\s+[¥￥]")


class XsollaParser:
    name = "xsolla"

    def matches(self, sender: str, subject: str, body: str) -> bool:
        return "xsolla" in sender.lower() or "Xsolla" in body

    def parse(self, sender: str, subject: str, body: str) -> ParsedReceipt | None:
        m = PRODUCT.search(body)
        if not m:
            return None
        product = " ".join(m.group(1).split())
        if not product or product.lower().startswith("xsolla"):
            return None
        item = ""
        i = ITEM.search(body)
        if i:
            item = " ".join(i.group(1).split())
        return ParsedReceipt(merchant=product[:60], item=item[:60], source_label="Xsolla")
