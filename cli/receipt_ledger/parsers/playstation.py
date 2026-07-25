"""PlayStation Store の購入明細。

送信元(reply@txn-email.playstation.com)は常に同じなので、
そのままだと全件「PlayStation」に潰れる。商品名は本文にある。

本文の該当部分:

    購入日: 20XX/XX/XX
    詳細 価格
    PlayStation Plusエッセンシャル : 1ヶ月利用権 (定額サービス)
    定額サービスの加入料: ¥850 が次の日付で請求されます: ...
    小計: ¥850
    合計: ¥850

商品名の行は「詳細」「価格」という見出しの直後に来る。
本文が text/plain か text/html かで空白と改行の入り方が変わるため、
改行に依存せず見出しからの位置で拾う。
"""

from __future__ import annotations

import re

from .base import ParsedReceipt

# 「詳細 価格」の直後の1項目。金額や次の見出しの手前まで。
DETAIL = re.compile(r"詳細\s+価格\s+(.{1,70}?)\s+(?:定額サービス|小計|合計|[¥￥])")


class PlayStationParser:
    name = "playstation"

    def matches(self, sender: str, subject: str, body: str) -> bool:
        s = sender.lower()
        return "playstation" in s or "PlayStation™Store" in body

    def parse(self, sender: str, subject: str, body: str) -> ParsedReceipt | None:
        m = DETAIL.search(body)
        if not m:
            return None
        title = " ".join(m.group(1).split())
        if not title:
            return None
        # 「商品名 : 内訳」の形なので、コロンの前を請求元、全体を品目にする
        merchant = title.split(":")[0].strip() if ":" in title else title
        return ParsedReceipt(
            merchant=(merchant or title)[:60], item=title[:60], source_label="PlayStation"
        )
