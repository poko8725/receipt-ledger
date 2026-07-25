"""Apple (App Store / iTunes) の領収書。

送信元は常に no_reply@email.apple.com なので、そのままだと全件
「Apple」に潰れる。アプリ名は本文にしかない。

レイアウトが2種類ある(実データ412通で確認):

  旧: 区切り線のあとに「アプリ名 ¥金額」が続く
      --------------------------------------------------
      パニシング：グレイレイヴン ¥1,500
      麗しの童夢パック（L）
      App内課金

  新: 「App Store」の次の行がアプリ名、その次が課金アイテム名
      App Store
      ドラゴンクエストウォーク 歩く楽しみが増える位置情報ゲーム
      ジェムパックC
      アプリ内課金

新レイアウトではアプリ名に宣伝文が付く。同じアプリが
「ドラゴンクエストウォーク」「〜 ドラクエの位置情報ゲーム」
「〜 歩く楽しみが増える位置情報ゲーム」の3通りで届くため、
表記の統一は canonicalize_merchants() (rules.py) が後段で行う。

制約: 1通に複数アプリの購入が入っている場合、合計金額は
最初のアプリに寄せられる。金額は1通1件で扱う設計のため。
"""

from __future__ import annotations

import re

from .base import ParsedReceipt

# 旧レイアウト: 区切り線 -> アプリ名 -> 金額
LAYOUT_OLD = re.compile(r"-{10,}\s*([^\n¥￥]{1,60}?)\s*[¥￥]\s*[\d,]+")

# 新レイアウト: App Store -> アプリ名 -> アイテム名 -> 課金種別
LAYOUT_NEW = re.compile(
    r"App\s*Store\s*\n\s*([^\n]{1,80}?)\s*\n\s*([^\n]{1,80}?)\s*\n\s*"
    r"(?:アプリ内課金|App内課金|自動更新)"
)

# 課金アイテム名がアプリ名の位置に来てしまう並びを弾くための語。
# 「8980水晶」「毎月更新 ジュエル 7500個」などはアプリ名ではない。
ITEM_LIKE = re.compile(r"^(?:\d|毎月更新|年間|月額)|(?:個|水晶|ジュエル|パック|チケット)$")


class AppleParser:
    name = "apple"

    def matches(self, sender: str, subject: str, body: str) -> bool:
        s = sender.lower()
        if "apple.com" in s or "itunes" in s:
            return True
        return "Apple 領収書" in body or "APPLE ACCOUNT" in body

    def parse(self, sender: str, subject: str, body: str) -> ParsedReceipt | None:
        app = ""
        item = ""

        m = LAYOUT_NEW.search(body)
        if m and not _is_separator(m.group(1)):
            app = " ".join(m.group(1).split())
            item = " ".join(m.group(2).split())
        else:
            m = LAYOUT_OLD.search(body)
            if m:
                app = " ".join(m.group(1).split())

        # アプリ名の位置に課金アイテム名しか無い領収書がある
        # (例: 「聖晶石 168個 ¥10,000」だけで、アプリ名の行が無い)。
        # その場合は請求元を Apple のままにして、品目だけ残す。
        # 品目からタイトルを引く処理(rules.title_from_item)に拾わせるため。
        if app and (_is_separator(app) or ITEM_LIKE.search(app)):
            item, app = app, ""

        if not app and not item:
            return None
        return ParsedReceipt(
            merchant=(app or "Apple")[:60], item=item[:60], source_label="Apple"
        )


def _is_separator(text: str) -> bool:
    """区切り線や記号だけの行をアプリ名と取り違えないようにする。"""
    stripped = text.strip()
    return not stripped or not set(stripped) - set("-—=_・* 　")
