"""RFC 822 のバイト列を1件のレコードに変換する。

取得元(Mail.app / .eml フォルダ / 将来の Gmail)を問わずここを通る。
ソースのことは何も知らない。
"""

from __future__ import annotations

import email
import email.message
import html
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from email.header import decode_header
from email.utils import parsedate_to_datetime

from .parsers import parse_receipt
from .rules import extract_amount, identify_merchant, title_from_item
from .sources.base import RawMessage


@dataclass
class Record:
    uid: str
    origin: str
    date: str
    month: str
    sender: str
    subject: str
    merchant: str
    item: str
    amount: Decimal
    currency: str = "JPY"
    mailbox: str = ""
    message_id: str = ""


def decode_mime_words(s: str) -> str:
    """MIME エンコードされた件名・送信元をデコードする。"""
    if not s:
        return ""
    decoded = ""
    for text, enc in decode_header(s):
        if isinstance(text, bytes):
            try:
                decoded += text.decode(enc or "utf-8", errors="ignore")
            except LookupError:
                decoded += text.decode("utf-8", errors="ignore")
        else:
            decoded += text
    return decoded


def _decode_bytes(raw: bytes, charset: str | None) -> str:
    """未知/不正な charset 名に備えて順に試す。日本語メールは iso-2022-jp も多い。"""
    for candidate in (charset, "utf-8", "shift_jis", "iso-2022-jp"):
        if not candidate:
            continue
        try:
            return raw.decode(candidate, errors="ignore")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="ignore")


def _html_to_text(raw: str) -> str:
    """HTML を本文テキストにする。

    タグを剥がすだけでは足りない。実データの PayPal 領収書は金額を
    「&yen;12,000 JPY」と実体参照で書いてくるため、デコードしないと
    通貨記号が見つからず品目も金額も取り違える。
    """
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def get_body_text(msg: email.message.Message) -> str:
    """本文を取り出す。text/plain 優先、無ければ text/html をタグ除去。"""
    if msg.is_multipart():
        for want in ("text/plain", "text/html"):
            for part in msg.walk():
                if part.get_content_type() != want:
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                text = _decode_bytes(payload, part.get_content_charset())
                return _html_to_text(text) if want == "text/html" else text
        return ""

    payload = msg.get_payload(decode=True)
    if payload is None:
        inner = msg.get_payload()
        return inner if isinstance(inner, str) else ""
    text = _decode_bytes(payload, msg.get_content_charset())
    if msg.get_content_type() == "text/html":
        text = _html_to_text(text)
    return text


def analyze(message: RawMessage) -> Record | None:
    """金額が取れなければ None(レシートとみなさない)。"""
    try:
        msg = email.message_from_bytes(message.raw)
    except Exception:
        return None

    message_id = (msg.get("Message-ID") or "").strip()
    sender = decode_mime_words(msg.get("From", ""))
    subject = decode_mime_words(msg.get("Subject", ""))

    dt: datetime | None
    try:
        dt = parsedate_to_datetime(msg.get("Date", ""))
    except (TypeError, ValueError):
        dt = None

    # 件名を先に見る。件名に金額があるものは信頼度が高い。
    # `or` ではなく None 判定にしないと、金額 0 円のときに本文へ落ちてしまう。
    body = get_body_text(msg)

    found = extract_amount(subject)
    if found is None:
        found = extract_amount(body)
    if found is None:
        return None
    amount, currency = found

    # 決済代行(PayPal 等)を挟むと送信元は請求元ではないので、
    # 本文を読めるフォーマットならそちらを優先する。
    receipt = parse_receipt(sender, subject, body)
    merchant = receipt.merchant if receipt else identify_merchant(sender, subject)
    item = receipt.item if receipt else ""

    # 運営会社が複数タイトルを出している場合、アイテム名からタイトルを引く。
    # 引けなければ請求元(運営会社)のまま。推測で間違えるより残すほうがよい。
    # 品目で引けないときは請求元名でも引く。アプリ名の位置に
    # 課金アイテム名が入っている領収書があり(「キャンペーン水晶5」など)、
    # そのままだとアイテム名が請求元として並んでしまう。
    title = title_from_item(item) or title_from_item(merchant)
    if title:
        merchant = title

    return Record(
        uid=message.uid,
        origin=message.origin,
        date=dt.strftime("%Y-%m-%d") if dt else "不明",
        month=dt.strftime("%Y-%m") if dt else "不明",
        sender=sender,
        subject=subject,
        merchant=merchant,
        item=item,
        message_id=message_id,
        amount=amount,
        currency=currency,
        mailbox=message.meta.get("mailbox", ""),
    )
