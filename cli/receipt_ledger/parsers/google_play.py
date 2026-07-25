"""Google Play のご注文明細。

本文の該当部分:

    Google Play での miHoYo Inc. からの購入が完了しました。
    注文番号: GPA.XXXX-XXXX-XXXX-XXXXX
    アイテム 価格
    280水晶（月パス） (崩壊3rd) ￥490
    合計: ￥490

ソシャゲ課金の集計では、開発元(miHoYo Inc.)よりアプリ名(崩壊3rd)で
まとめたいことが多い。同じ開発元が複数タイトルを出しているため。
アイテム行の末尾の括弧がアプリ名なので、取れたらそちらを請求元に使い、
無ければ開発元に落とす。
"""

from __future__ import annotations

import re

from ..rules import normalize_merchant
from .base import ParsedReceipt

DEVELOPER = re.compile(r"Google Play での\s*(.+?)\s*からの購入")
ITEM_LINE = re.compile(r"アイテム\s*価格\s*\n?\s*(.+?)\s*[¥￥]\s*[\d,]+")

# 「280水晶（月パス） (崩壊3rd)」の末尾側、半角括弧がアプリ名。
# 全角括弧はアイテム名の一部なので対象にしない。
APP_IN_ITEM = re.compile(r"\(([^()]{1,40})\)\s*$")


class GooglePlayParser:
    name = "google-play"

    def matches(self, sender: str, subject: str, body: str) -> bool:
        return "googleplay-noreply@google.com" in sender.lower() or "Google Play での" in body

    def parse(self, sender: str, subject: str, body: str) -> ParsedReceipt | None:
        dev = DEVELOPER.search(body)
        item_text = ""
        app = ""

        i = ITEM_LINE.search(body)
        if i:
            item_text = " ".join(i.group(1).split())[:60]
            a = APP_IN_ITEM.search(item_text)
            if a:
                app = a.group(1).strip()

        merchant = app or (normalize_merchant(dev.group(1)) if dev else "")
        if not merchant:
            return None
        return ParsedReceipt(merchant=merchant, item=item_text, source_label="Google Play")
