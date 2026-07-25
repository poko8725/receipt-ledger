"""領収書フォーマット別パーサーの共通インタフェース。

sources/ が「どこからメールを取るか」を切り離しているのに対して、
parsers/ は「その領収書をどう読むか」を切り離す。

これが必要な理由:

    決済代行を挟むと、送信元は請求元と一致しない。
    PayPal 経由の課金は全部 service-jp@paypal.com から届くので、
    送信元だけ見ていると全件「PayPal」に潰れて内訳が消える。
    実際の請求先は本文の「マーチャント」欄にある。
    Google Play も同じで、開発元とアプリ名は本文にしかない。

パーサーが1つも当たらなければ、rules.identify_merchant()
(送信元アドレスからの推測)にそのまま落ちる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ParsedReceipt:
    """領収書から読み取れた、送信元だけでは分からない情報。"""

    merchant: str
    """実際の請求先。正規化済みの表示名。"""

    item: str = ""
    """品目(課金アイテム名やアプリ名)。分からなければ空。"""

    source_label: str = ""
    """どのパーサーが解釈したか。表示と検証用。"""


@runtime_checkable
class ReceiptParser(Protocol):
    """特定の領収書フォーマットを読む。

    新しいフォーマットを足す手順:
      1. このプロトコルを満たすクラスを parsers/ に1ファイルで書く
      2. parsers/__init__.py の PARSERS に登録する
    金額抽出(rules.extract_amount)と集計側は触らない。
    """

    name: str

    def matches(self, sender: str, subject: str, body: str) -> bool:
        """この領収書を自分が読めるか。安く判定できる条件だけを見る。"""
        ...

    def parse(self, sender: str, subject: str, body: str) -> ParsedReceipt | None:
        """読めなければ None。呼び出し側は送信元推測にフォールバックする。"""
        ...
