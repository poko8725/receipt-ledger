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
from .rules import (extract_amount, identify_merchant, non_transaction_reason,
                    title_from_item)
from .sources.base import RawMessage


class UnsupportedFormat(Exception):
    """解析できない形式。黙って捨てずに、呼び出し側へ知らせる。"""


@dataclass
class Record:
    uid: str
    origin: str

    date: str
    """YYYY-MM-DD。**手元のタイムゾーンでの日付**で、Date ヘッダに書かれた
    ままの日付ではない。期間の絞り込みも月別集計もこの値を使う。"""

    month: str
    sender: str
    subject: str
    merchant: str
    """表示用の請求元。運営会社名がタイトルに置き換わっていることがある。"""


    item: str
    amount: Decimal
    currency: str = "JPY"
    mailbox: str = ""
    message_id: str = ""

    non_transaction: str = ""
    """取引でないと判断した理由。取引なら空。

    ここで捨てずに理由を載せて返すのは、**落とした件数と中身を利用者に見せる**ため。
    黙って None を返すと「金額が取れなかった」と同じ扱いになり、合計が静かに減る。
    """

    billed_by: str = ""
    """タイトルで上書きする前の請求元。取引の相手方を出したいときはこちら。

    支出の内訳を見る用途では、運営会社名より作品名のほうが有用なので merchant を
    置き換えている。一方、証憑の索引のように**取引の相手方**が要る用途では、
    商品名が入っていると使えない。同じ「請求元」でも用途で定義が違うので、
    置き換える前の値を捨てずに残す。
    """


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


def local_datetime(value: object) -> datetime | None:
    """Date ヘッダを手元のタイムゾーンの日時にする。読めなければ None。

    Date ヘッダは送信側のタイムゾーンで書かれている。米国の事業者からの
    領収書なら `-0700` のように届くので、**書かれた日付をそのまま使うと
    利用者の暦とずれる**。実際に -0700 の 7/15 10:43〜10:59 に届いた
    PayPal の領収書6通(JST では 7/16 02:43〜02:59)が、
    `--since 2026-07-16` で「期間より前」と判定されて落ちた。

    利用者が --since に書く日付も、経費を締める月も手元の暦なので、
    そちらに合わせる。tzinfo が無い Date(`-0000` や記載なし)は
    astimezone() が手元のタイムゾーンとみなすため、日付は動かない。
    """
    # Date に非 ASCII が混じっていると get() は str ではなく Header を返し、
    # parsedate_to_datetime が AttributeError で落ちる(1通で全体が止まる)。
    # 読めない日付は「不明」で通ればよいので、文字列にしてから渡す。
    try:
        dt = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if dt is None:      # 版によっては例外ではなく None が返る
        return None
    try:
        return dt.astimezone()
    except (OverflowError, OSError, ValueError):
        # 極端な日付(西暦9999年など)は、手元の暦へ直すと表現範囲を外れる。
        # 日付が無いものとして扱う(この後の絞り込みでは落とさない側に倒れる)。
        return None


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


def _part_text(msg: email.message.Message, want: str) -> str:
    """multipart から最初の `want` パートを平文にして返す。無ければ空。"""
    for part in msg.walk():
        if part.get_content_type() != want:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = _decode_bytes(payload, part.get_content_charset())
        return _html_to_text(text) if want == "text/html" else text
    return ""


def get_body_text(msg: email.message.Message) -> str:
    """本文を取り出す。text/plain 優先。**金額が無ければ text/html も継ぎ足す。**

    text/plain があればそこで返していたが、**同じメールの text/html にだけ
    金額が入っている形がある。**Steam の購入確認がこれで、text/plain は

        詳細を確認するには、https://store.steampowered.com/email/... をご覧ください

    という案内だけ(3KB)、text/html(57KB)に品目・小計・消費税・合計・
    インボイス番号・発行日が全部入っている。**片方のパートしか見ていなかった。**

    `parsers/__init__.py` に「Steam はメール本文に明細が無く、リンク先の Web
    ページにしかない」と対応不能として書いてあったが、**あれは誤り**で、
    本文には最初から入っていた。読めなかったのはここの選び方のせいだった。

    継ぎ足しであって差し替えではない。text/plain にしか無い情報を捨てないため。
    金額が読めているときは html を触らない(大半が広告なので、通数に比例する
    処理を増やさない)。

    2026-08-08 の実測(Apple Mail 8,723 通)では、これで新たに読めたのが 216 通、
    うち 152 通は非取引として弾かれ、証憑として残るのが 64 通。
    内訳は Steam の購入確認 52・Amazon の注文確認 3 が本物で、
    残り7件ほどは本文の `2022` を年号ではなく ¥2,022 と読むような誤りだった
    (すべて 2023 年以前。金額抽出側の弱点で、ここで生まれたものではない)。
    """
    if msg.is_multipart():
        text = _part_text(msg, "text/plain")
        if text and extract_amount(text) is not None:
            return text
        html_text = _part_text(msg, "text/html")
        if not text:
            return html_text
        return f"{text}\n{html_text}" if html_text else text

    payload = msg.get_payload(decode=True)
    if payload is None:
        inner = msg.get_payload()
        return inner if isinstance(inner, str) else ""
    text = _decode_bytes(payload, msg.get_content_charset())
    if msg.get_content_type() == "text/html":
        text = _html_to_text(text)
    return text


# OLE2 複合ファイルの署名。Outlook の .msg はこの形式で、RFC 822 ではない。
# 拡張子を変えただけのものや、.msg をそのまま渡された場合にここで止まる。
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def analyze(message: RawMessage) -> Record | None:
    """金額が取れなければ None(レシートとみなさない)。

    .msg を渡されると、email モジュールは例外を投げずにヘッダ 0 件の
    メッセージを返す。**黙って「解析できたが金額が無い」ように見える**ので、
    形式の段階で弾く。経費処理で使う場合、静かに欠測するほうが害が大きい。
    """
    if message.raw[:8] == OLE2_MAGIC:
        raise UnsupportedFormat(
            f"{message.origin or message.uid} は .msg 形式（Outlook 独自）です。\n"
            "  RFC 822 ではないので解析できません。.eml で書き出してください。"
        )
    try:
        msg = email.message_from_bytes(message.raw)
    except Exception:
        return None

    message_id = (msg.get("Message-ID") or "").strip()
    sender = decode_mime_words(msg.get("From", ""))
    subject = decode_mime_words(msg.get("Subject", ""))

    dt = local_datetime(msg.get("Date", ""))

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
    billed_by = merchant
    title = title_from_item(item) or title_from_item(merchant)
    if title:
        merchant = title

    return Record(
        non_transaction=non_transaction_reason(subject, merchant, body, amount),
        uid=message.uid,
        origin=message.origin,
        date=dt.strftime("%Y-%m-%d") if dt else "不明",
        month=dt.strftime("%Y-%m") if dt else "不明",
        sender=sender,
        subject=subject,
        merchant=merchant,
        billed_by=billed_by,
        item=item,
        message_id=message_id,
        amount=amount,
        currency=currency,
        mailbox=message.meta.get("mailbox", ""),
    )
