"""照合用の .eml を生成する。

実データは使わない。差出人・金額・IDはすべて架空のものを組み立てている。
狙いは「それらしいメール」ではなく、**過去に壊れた箇所を狙って踏ませる入力**を作ること。

なぜ手書きの .eml をリポジトリに置かず生成するのか:
  - ISO-2022-JP のエスケープ列や base64 は、テキストエディタで書くと壊れる
  - どのバイトが何を狙っているのかが、生成コードにしか書けない
  - 読む側が中身を変えて試せる

生成:
    python3 compare/make_fixtures.py
"""

from __future__ import annotations

import base64
import quopri
from pathlib import Path

OUT = Path(__file__).parent / "fixtures"


def crlf(text: str) -> bytes:
    """ヘッダは CRLF で組む(RFC 5322)。本文の改行は各 fixture の意図に任せる。"""
    return text.replace("\n", "\r\n").encode("ascii")


# ---------------------------------------------------------------- 01
def iso2022jp_escape() -> bytes:
    """7bit の ISO-2022-JP を復号せずに扱うと $ が通貨記号に化ける。

    ISO-2022-JP の2バイト文字は、そのままバイト列として読むと ASCII に見える。
    ひらがなは第1バイトが 0x24、つまり '$' である。
    「す」は 0x24 0x39 なので、バイト列では "$9" と読める。
    その直後に第1バイトが 0x30('0')〜0x39('9') の漢字が来ると、"$90" が出来上がる。

    金額抽出は「通貨記号のあとに数字」を拾うので、復号せずに正規表現を当てると
    これをドル建て金額として読む。正しく復号すれば、ただの日本語として通り過ぎる。

    復号できている実装  : JPY 1780(「合計 1,780円」を読む)
    復号していない実装  : USD 90   (「合計」がバイト列のままなので読めず、"$90" を拾う)

    狙って作ってはいるが、必要なのは「ひらがなの直後に特定の区の漢字が来る」だけで、
    日本語の平文なら普通に起こりうる並びである。
    """
    trap = bytes([0x30, 0x24])  # 第1バイトが '0' の漢字。直前の「す」の "$9" と繋がる
    body = (
        b"\x1b$B"
        + "ご利用ありがとうございます".encode("iso-2022-jp")[3:-3]
        + trap
        + b"\x1b(B\n"
        + "合計 1,780円\n".encode("iso-2022-jp")
        + b"\n"
    )
    head = crlf(
        "From: DMM <info@mail.dmm.example>\n"
        "To: user@example.com\n"
        "Subject: =?ISO-2022-JP?B?"
        + base64.b64encode("ご購入明細".encode("iso-2022-jp")).decode()
        + "?=\n"
        "Date: Tue, 13 Jan 2026 12:34:56 +0900\n"
        "Message-ID: <fixture-01@example.invalid>\n"
        "Content-Type: text/plain; charset=ISO-2022-JP\n"
        "Content-Transfer-Encoding: 7bit\n"
        "\n"
    )
    return head + body


# ---------------------------------------------------------------- 02
def html_entity_amount() -> bytes:
    """実体参照を解決しないと、通貨記号を見失って別の数字を拾う。

    最初は「&yen;12,000 JPY」で書いていたが、これは**解決しなくても通ってしまう**。
    ラベルと数字の間の実体参照を読み飛ばしても、12,000 と JPY が残るためである。
    prove.py に欠陥を戻させて初めて、このフィクスチャが何も検査していないと分かった。

    通貨記号そのものを実体参照にすると、解決の有無が結果を変える。

        解決する   : 「合計 $43.00」  → USD 43
        解決しない : 「合計 &#36;43.00」→ 通貨記号が無いので "36" を金額として拾う
    """
    html = (
        "<html><body><table>"
        "<tr><td>合計</td><td>&#36;43.00</td></tr>"
        "<tr><td>お支払い方法</td><td>クレジットカード</td></tr>"
        "</table></body></html>\n"
    )
    head = crlf(
        "From: Example Store <billing@store.example>\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode("ご購入の領収書".encode()).decode() + "?=\n"
        "Date: Wed, 14 Jan 2026 10:00:00 +0900\n"
        "Message-ID: <fixture-02@example.invalid>\n"
        "Content-Type: text/html; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    return head + html.encode("utf-8")


# ---------------------------------------------------------------- 03
def mime_word_split() -> bytes:
    """RFC 2047 の分割語。encoded-word が続くとき、間の空白は捨てる。

    「ご購入」「明細のお知らせ」を2つの encoded-word に割る。
    折り返しの空白を残すと「ご購入 明細のお知らせ」になり、
    捨てると「ご購入明細のお知らせ」になる。件名は請求元判定にも使われるので、
    ここが実装ごとに違うと結果が静かにずれる。
    """
    w1 = base64.b64encode("ご購入".encode()).decode()
    w2 = base64.b64encode("明細のお知らせ".encode()).decode()
    head = crlf(
        "From: Example Store <billing@store.example>\n"
        "To: user@example.com\n"
        f"Subject: =?UTF-8?B?{w1}?=\n =?UTF-8?B?{w2}?=\n"
        "Date: Thu, 15 Jan 2026 11:00:00 +0900\n"
        "Message-ID: <fixture-03@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "\n"
    )
    return head + "お支払金額 ¥2,400\n".encode("utf-8")


# ---------------------------------------------------------------- 04
def proxy_merchant() -> bytes:
    """決済代行を挟むと、送信元は請求元ではない。

    送信元だけを見る実装はこれを「PayPal」に潰す。
    本文の「マーチャント」欄を読めば、実際の請求先が出る。
    """
    body = (
        "取引が完了しました\n"
        "\n"
        "取引ID XXXXXXXXXXXXXXXXX\n"
        "マーチャント EXAMPLE GAMES PTE. LTD...\n"
        "説明 単価 数量 金額\n"
        "星の砂 1000個 ¥1,220 JPY 1 ¥1,220 JPY\n"
        "合計 ¥1,220 JPY\n"
    )
    head = crlf(
        "From: service-jp@paypal.example\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode("お支払いの領収書".encode()).decode() + "?=\n"
        "Date: Fri, 16 Jan 2026 20:15:00 +0900\n"
        "Message-ID: <fixture-04@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    return head + body.encode("utf-8")


# ---------------------------------------------------------------- 05
def base64_utf8() -> bytes:
    """base64 + UTF-8。復号経路が違っても結果は同じになるはず、という確認。"""
    body = base64.b64encode("お買い上げ明細\n合計金額 ¥3,240\n".encode("utf-8"))
    wrapped = b"\r\n".join(body[i : i + 76] for i in range(0, len(body), 76)) + b"\r\n"
    head = crlf(
        "From: Example Mart <receipt@mart.example>\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode("ご注文の明細".encode()).decode() + "?=\n"
        "Date: Sat, 17 Jan 2026 09:05:00 +0900\n"
        "Message-ID: <fixture-05@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: base64\n"
        "\n"
    )
    return head + wrapped


# ---------------------------------------------------------------- 06
def qp_iso2022jp() -> bytes:
    """quoted-printable + ISO-2022-JP。復号を2段通す経路。"""
    raw = "ご利用明細\n決済金額 5,980円\n".encode("iso-2022-jp")
    body = quopri.encodestring(raw)
    head = crlf(
        "From: Example Net <info@net.example>\n"
        "To: user@example.com\n"
        "Subject: =?ISO-2022-JP?B?"
        + base64.b64encode("ご利用明細".encode("iso-2022-jp")).decode()
        + "?=\n"
        "Date: Sun, 18 Jan 2026 22:40:00 +0900\n"
        "Message-ID: <fixture-06@example.invalid>\n"
        "Content-Type: text/plain; charset=ISO-2022-JP\n"
        "Content-Transfer-Encoding: quoted-printable\n"
        "\n"
    )
    return head + body


# ---------------------------------------------------------------- 07
def nested_multipart() -> bytes:
    """multipart/mixed > multipart/alternative + 添付。

    text/plain を選べているか。html を先に拾うと金額が別の行から取れてしまう。
    添付(application/pdf)を本文と誤認しないかも同時に見る。
    """
    plain = "領収書\n合計 ¥8,800\n"
    html = "<html><body><p>合計 &yen;9,999</p></body></html>"  # わざと違う金額
    pdf = base64.b64encode(b"%PDF-1.4 dummy").decode()
    body = (
        "--MIXED\n"
        "Content-Type: multipart/alternative; boundary=\"ALT\"\n"
        "\n"
        "--ALT\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
        f"{plain}\n"
        "--ALT\n"
        "Content-Type: text/html; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
        f"{html}\n"
        "--ALT--\n"
        "--MIXED\n"
        "Content-Type: application/pdf; name=\"receipt.pdf\"\n"
        "Content-Transfer-Encoding: base64\n"
        "\n"
        f"{pdf}\n"
        "--MIXED--\n"
    )
    head = crlf(
        "From: Example Shop <order@shop.example>\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode("ご注文ありがとうございます".encode()).decode() + "?=\n"
        "Date: Mon, 19 Jan 2026 13:20:00 +0900\n"
        "Message-ID: <fixture-07@example.invalid>\n"
        "Content-Type: multipart/mixed; boundary=\"MIXED\"\n"
        "\n"
    )
    return head + body.replace("\n", "\r\n").encode("utf-8")


# ---------------------------------------------------------------- 08
def timezone_boundary() -> bytes:
    """日付が UTC 換算で前日になる時刻。

    +0900 の 08:30 は UTC では前日 23:30 になる。
    どちらの日付で記録するかは実装の選択であって、揃っていなければ集計月がずれる。
    """
    head = crlf(
        "From: Example Store <billing@store.example>\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode("早朝のご購入".encode()).decode() + "?=\n"
        "Date: Thu, 15 Jan 2026 08:30:00 +0900\n"
        "Message-ID: <fixture-08@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    return head + "合計 ¥1,100\n".encode("utf-8")


# ---------------------------------------------------------------- 09
def html_cell_boundary() -> bytes:
    """タグを空白に置き換えず削除すると、隣のセルの数字と連結する。

    表組みの領収書で、金額の直前のセルが数字で終わっていると事故になる。

        空白に置換 : 「1,234 5,600円」 → 5,600 を拾う
        削除       : 「1,2345,600円」  → 12,345,600 を拾う

    ラベル（合計・ご請求金額など）を置いていないのは、ラベルがあると
    そちらから読めてしまい、この欠陥を踏まなくなるため。
    """
    html = (
        "<html><body><table>"
        "<tr><th>ポイント</th><th>金額</th></tr>"
        "<tr><td>1,234</td><td>5,600円</td></tr>"
        "</table></body></html>\n"
    )
    head = crlf(
        "From: Example Mart <receipt@mart.example>\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode("お買い上げ明細".encode()).decode() + "?=\n"
        "Date: Tue, 20 Jan 2026 15:00:00 +0900\n"
        "Message-ID: <fixture-09@example.invalid>\n"
        "Content-Type: text/html; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    return head + html.encode("utf-8")


FIXTURES = {
    "01-iso2022jp-escape.eml": iso2022jp_escape,
    "02-html-entity-amount.eml": html_entity_amount,
    "03-mime-word-split.eml": mime_word_split,
    "04-proxy-merchant.eml": proxy_merchant,
    "05-base64-utf8.eml": base64_utf8,
    "06-qp-iso2022jp.eml": qp_iso2022jp,
    "07-nested-multipart.eml": nested_multipart,
    "08-timezone-boundary.eml": timezone_boundary,
    "09-html-cell-boundary.eml": html_cell_boundary,
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, build in FIXTURES.items():
        (OUT / name).write_bytes(build())
        print(f"生成: fixtures/{name}")


if __name__ == "__main__":
    main()
