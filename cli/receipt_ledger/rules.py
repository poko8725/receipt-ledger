"""請求元判定と金額抽出のルール。

CLI 側の正。ここを直せば全ソース(Mail.app / .eml / 将来の Gmail)に効く。

注意: index.html にも同じ内容の JS 版がある。ブラウザ版はビルド無しの
HTML 1枚を維持しているため、現状は手で同期させる必要がある。
片方だけ直すと挙動がずれるので、ルールを足すときは必ず両方を見ること。
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

# キー: 表示名 / 値: 送信元アドレスや件名に含まれるキーワード
MERCHANT_RULES: dict[str, list[str]] = {
    "Amazon": ["amazon.co.jp", "auto-confirm@amazon"],
    "楽天市場": ["rakuten.co.jp", "rakuten market"],
    "Apple": ["apple.com", "no_reply@email.apple.com"],
    "セブン-イレブン": ["7-eleven", "sej.co.jp"],
    "ファミリーマート": ["family.co.jp", "familymart"],
    "ローソン": ["lawson.co.jp"],
    "Uber Eats": ["uber.com", "ubereats"],
    "Netflix": ["netflix.com"],
    "Spotify": ["spotify.com"],
}

UNKNOWN_MERCHANT = "不明"

# 通貨の表記ゆれ -> ISO コード
CURRENCY_BY_TOKEN = {
    "¥": "JPY", "￥": "JPY", "円": "JPY", "JPY": "JPY",
    "$": "USD", "US$": "USD", "USD": "USD",
    "€": "EUR", "EUR": "EUR",
    "£": "GBP", "GBP": "GBP",
}

CURRENCY_SYMBOL = {"JPY": "¥", "USD": "$", "EUR": "€", "GBP": "£"}

# 通貨表記が無いときの既定。日本のレシートが主対象のため。
DEFAULT_CURRENCY = "JPY"

# 小数ありの金額($12.34)と、2桁以上の整数(3,980)の両方を受ける。
# 整数側は「数字で始まり数字で終わる」で桁数を担保する。
# \d{2} 始まりにすると 3,980 の "3," で落ちるため使えない。
# 1桁だけの数字は誤検出が多いので拾わない(その代わり 0 円は取れない)。
# 繰り返しに上限を置いているのは ReDoS 対策。`[\d,]*` にすると、
# 小数点を含まない長い数字列で「全部消費して失敗、1文字戻してまた失敗」を
# 繰り返し、2万文字で約10秒かかる。メールは第三者が送れるので、
# 一括処理を止められることになる。金額が20桁を超えることは無い。
_NUM = r"(?:\d[\d,]{0,19}\.\d{1,2}|\d[\d,]{0,19}\d)"
_PRE = r"(?:US\$|[¥￥$€£]|USD|JPY|EUR|GBP)"
_SUF = r"(?:円|USD|JPY|EUR|GBP)"
_LABEL = (
    r"(?:合計金額|合計|ご請求金額|ご利用金額|お支払金額|お支払い金額|決済金額|請求額|総額"
    r"|Order Total|Grand Total|Total|Amount charged|Amount paid|Amount due|Amount)"
)

# 上から順に試す。ラベル付き(合計/Total など)を最優先にし、
# 単なる「¥1,234」を拾うのは最後にする。順番に意味がある。
# ラベルと数字の間で通貨記号を食い潰さないよう、記号は除外して読み飛ばす。
#
# **空白は読み飛ばしの上限に数えない。**上限そのものはラベルが遠くの
# 無関係な数字に当たるのを防ぐために要るが、HTML のテーブルを平文にすると
# セルの間に空白と改行が数十文字並ぶ。実データの Apple の領収書は
# 「合計」と「¥3,850」の間が約50文字の空白で、12文字の上限を超えて
# ラベル付きが不成立になり、裸の「¥650」(明細の1件目)を拾っていた。
# 401通中14通、合計 ¥27,660 が黙って欠けた(2026-08-05 に実測)。
#
# 空白と非空白でクラスを分けているのは、両者が重なると
# 「空白をどちらが食うか」の組み合わせが爆発するため(ReDoS)。
# 重ならないので後戻りは起きないが、上限どうしの積が探索量になる。
#
# 上限64は実測から決めた。手元の1500通でラベル直後に金額が来る674箇所を測ると
# 中央値25・p99 53・**最大53**で、65文字以上は1件も無かった。
# 空白の実行を2箇所に絞ってあるので、**ラベルから金額まで届く空白は最大72文字**
# (64+8。二分探索で確認)。実測の最大53に対して余裕19文字。
# 一度、空白の実行を3箇所・上限400で書いたときは到達距離が192文字まで伸び、
# 敵対的な入力(空白6万字+非空白6万字)で 0.826s かかった。
# メールは第三者が送れるので、1通あたりの上限時間も到達距離も小さく保つ。
_GAP = r"[^\d¥￥$€£\s]"
AMOUNT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        # 読み飛ばしは最短一致にする。貪欲だと "Total: USD 49.99" の USD を
        # 食い潰して通貨不明(=既定の円)に化ける。
        rf"{_LABEL}(?:{_GAP}{{0,12}}?\s{{0,64}})?"
        rf"(?P<pre>{_PRE})?\s{{0,8}}(?P<num>{_NUM})\s*(?P<suf>{_SUF})?",
        re.IGNORECASE,
    ),
    re.compile(rf"(?P<pre>{_PRE})\s*(?P<num>{_NUM})", re.IGNORECASE),
    re.compile(rf"(?P<num>{_NUM})\s*(?P<suf>{_SUF})", re.IGNORECASE),
]


# 金額の形はしているが、支払った額ではない文脈。
#
# 実測で拾ってしまった4件が、それぞれ別の形をしていた。
#
#   「Amazonギフトカード（※）3,000円相当」   → 当選賞品の価値
#   「3,980円(税込)以上ご購入で送料無料」     → 送料無料のしきい値
#   「新料金：¥2,500 — 旧料金：¥1,980」       → 値上げの予告。まだ払っていない
#   「合計で星玉×2,550や星軌専用チケット×10」 → ゲーム内通貨の個数
#
# どれも例外を出さず「もっともらしい金額」として通るので、**合計が静かに膨らむ**。
# 7月後半の実データでは、誤検出だけで ¥12,030 あった。
#
# 数字の直前・直後だけを見る。離れた語まで見に行くと、本物のレシートの脚注
# (「1,000円以上のご購入で送料無料」など)に引っかかって本物のほうを落とす。
_NOT_PAID_BEFORE = re.compile(
    r"(?:×|✕|新料金|旧料金|現行料金|改定後|改定前|値上げ後|値下げ後)"
    r"[\s:：=＝¥￥$€£]*$"
)
_NOT_PAID_AFTER = re.compile(
    # 括弧書き(「(税込)」など)を1つだけ挟むのは許して、その先で判定する。
    # 通貨の後置(「円」など)も挟んでよい。ラベル付きの一致(「総額 116,570」)は
    # 数字で終わるため、直後が「円の品が」となって判定を外していた。
    r"^\s*(?:円|JPY|USD|EUR|GBP)?\s*(?:\([^()（）]{0,8}\)|（[^()（）]{0,8}）)?\s*"
    # 「総額116,570円の品が 80,000 円」のように、値引き前の定価を提示してから
    # 実売価格を出す販促文がある。**ラベル(総額/合計)が付いているので、金額としては
    # 領収書と同じ形に見える**。直後の「の品が」「のところ」で定価だと分かる。
    r"(?:相当|以上|以下|未満|ポイント|ﾎﾟｲﾝﾄ|OFF|オフ|割引|引き|還元|お得|分の"
    r"|の品|のところ|の商品が|するところ"
    # 「200円につき1マイル」はレート、「合計50回分の導き」は回数。
    # どちらも広告の本文にあり、ラベル付きで書かれることもあるので金額に見える。
    r"|につき|あたり|回|個|点|名|件|枚|本|冊|台|マイル"
    # 「USD 19.99/year」はプランの料率。カート放棄を促す広告に出る形で、
    # 払った額ではない。領収書側は「Amount charged」等のラベルが先に当たる。
    r"|/\s*(?:year|month|mo|yr|week|day)\b|／\s*(?:年|月|週|日))",
    re.IGNORECASE,
)


def is_paid_amount(text: str, match: re.Match[str]) -> bool:
    """その一致が「実際に払った額」として読めるか。"""
    before = text[max(0, match.start("num") - 12) : match.start("num")]
    after = text[match.end() : match.end() + 24]
    return not (_NOT_PAID_BEFORE.search(before) or _NOT_PAID_AFTER.match(after))


# 1つの取引の進み具合を知らせる件名。**これが無ければ寄せない。**
#
# (請求元・金額・日付) だけで寄せると潰しすぎる。同じ日に同じ額を何度も払うのは
# 実データでは普通に起きていて、7月16日には同じ相手から ¥610 の領収書が3通来ていた。
# 課金の単位が決まっている相手ほどこうなるので、**この道具の主な用途で一番よく踏む**。
#
# 当たらなければ寄せずに残す。勝手に推測して合計を減らすより、そのほうが直しやすい。
_PROGRESS_SUBJECT = re.compile(
    r"注文|ご注文|発送|出荷|お届け|配送|到着|"
    r"order\s*(?:confirm|placed|received)|your\s+order|shipp|dispatch|"
    r"on\s+its\s+way|out\s+for\s+delivery|delivered",
    re.IGNORECASE,
)


# 件名に「取引があった」ことを示す語。**1つも無ければ取引ではない**と判定する。
#
# 金額の文脈(is_paid_amount)を見るだけでは、そのメールが取引かどうかは分からない。
# 広告メールに載っている製品価格、私信の本文に書かれた金額、値上げ予告の旧料金は、
# どれも「支払われた額」の形をしているので文脈判定を素通りする。
# 2025-01 以降の実データ 613 件では、この語が件名に1つも無いものはすべて非取引だった
# (Apple の新製品案内 ¥599,800、私信 ¥66,888 ×5 など)。
#
# **弾く側なので、増やすときは取りこぼしのほうを疑うこと。**
# 領収書なのに落ちると、合計が静かに小さくなって気づけない。
TRANSACTION_EVIDENCE = re.compile(
    r"領収書|レシート|受領|注文|購入|決済|支払|明細|請求|お届け|"
    r"チャージ完了|チャージが完了|受付|承りました|"
    r"receipt|invoice|order|payment|purchase|charged|confirmation|billing",
    re.IGNORECASE,
)

# カード会社が送る請求額の通知。件名に「支払」「請求」が入るので上の判定は通るが、
# 中身は**個々の取引ではなく1か月の合計**なので、明細と一緒に数えると二重になる。
# 発行元で落とすしかない(件名だけでは領収書と区別が付かない)。
STATEMENT_ISSUERS = {
    "Moneytree", "三井住友カード", "エポスカード", "楽天カード", "JCB", "セゾンカード",
}

# 値上げ・改定の予告。新旧2つの金額が本文に並ぶので、片方を拾ってしまう。
# 実際に課金された額は別途、通常の領収書として届く。
_REVISION_SUBJECT = re.compile(
    r"料金の改定|価格の改定|改定のお知らせ|price\s+change", re.IGNORECASE
)


# 「合計」「Total」などのラベルが付いた金額。**領収書の本文にしか出ない形**。
#
# 件名だけで判定すると、件名が素っ気ない領収書(件名が "PayPal" だけ、など)を
# 落としてしまう。落ちても例外は出ず、合計が静かに小さくなるだけなので気づけない。
# 一方、本文を全部見るのは弾く力が落ちる。Apple の広告メールはフッタに
# 「Apple Account ・ 購入履歴 ・ 販売条件」と書いてあり、"購入" が本文に出てしまう。
#
# ラベル付きの金額なら、その差を分けられる。広告に載る製品価格は
# 「¥599,800」と裸で置かれ、「合計 ¥599,800」とは書かれない。
# 本文のどこかにラベル付きの金額があるだけでは足りない。**拾った金額そのものに
# ラベルが付いているか**まで見る。販促メールにも「セット合計」のような別の合計が
# 載っていることがあり、本文全体を見ると、そこに救われて広告が通ってしまう。
_LABELED_AMOUNT = re.compile(
    _LABEL + r"\s*[:：]?\s*(?:" + _PRE + r")?\s*(?P<num>" + _NUM + r")", re.IGNORECASE
)


def has_labeled_amount(body: str, amount: Decimal) -> bool:
    """本文で、その金額に「合計」「Total」などのラベルが付いているか。"""
    for m in _LABELED_AMOUNT.finditer(body):
        try:
            if Decimal(m.group("num").replace(",", "")) == amount:
                return True
        except InvalidOperation:
            continue
    return False


def non_transaction_reason(
    subject: str, merchant: str, body: str = "", amount: Decimal | None = None
) -> str | None:
    """このメールが取引でないと判断できる理由。取引なら None。

    **呼び出し側は、落とした件数と中身を必ず利用者に見せること。**
    黙って落とすと、合計が小さくなったことに気づけない。
    """
    # 完全一致では取りこぼす。同じ発行元が「楽天カード」「楽天カード株式会社」の
    # 両方で届き、法人格を付けたほうだけ素通りしていた。
    if any(issuer in merchant for issuer in STATEMENT_ISSUERS):
        return "カード明細通知（合計であって取引ではない）"
    if _REVISION_SUBJECT.search(subject):
        return "料金改定の予告（実際の課金は別途届く）"
    if TRANSACTION_EVIDENCE.search(subject):
        return None
    if amount is not None and body and has_labeled_amount(body, amount):
        return None     # 件名は素っ気ないが、その金額が本文で合計として立っている
    return "取引を示す語が件名に無い（広告・通知・私信）"


def is_same_transaction(a, b) -> bool:
    """2件が「1つの取引の別々の通知」として読めるか。金額と日付は判定済みの前提。

    条件は2つ。

    **件名が違うこと。**同じ件名で届くのは、1件ごとに1通出す定型の相手である
    (「…様への支払いの領収書」)。同じ文面が並んでいるなら、それは同じ取引の
    別の段階ではなく、別々の取引が同じ形で届いている。

    **どちらかが進み具合を知らせていること。**「注文済み」と「発送済み」のように、
    件名から段階が読めるときだけ寄せる。読めないものは寄せない。
    """
    if a.subject == b.subject:
        return False
    return bool(_PROGRESS_SUBJECT.search(a.subject) or _PROGRESS_SUBJECT.search(b.subject))


# 決済代行そのものが請求元として残った記録。
#
# PayPal の領収書は本文の「マーチャント」欄から実際の請求先を読むが、
# 定期支払いの通知など、その欄が無い形式で届くことがある。読めなかったものは
# 請求元が「PayPal」のまま残る。**このとき、同じ支払いを加盟店側も別途知らせてくる**
# ので、1回の支払いが2件になる。実データでは 2 件あった
# (鳴潮 ¥610 / MuMuPlayer Pro $72.00)。
#
# 請求元が違うので通常の重複判定では寄らない。決済代行として残っているという
# ことは「相手が分からなかった通知」なので、同額・同通貨・近い日付の
# 加盟店側の記録があれば、それと同じ取引だと見てよい。
PAYMENT_PROCESSORS = {"paypal", "stripe", "square", "amazon pay", "google pay", "apple pay"}


def is_payment_processor(merchant: str) -> bool:
    """請求元が決済代行のまま残っているか（＝実際の相手を読めなかった通知）。"""
    return (merchant or "").strip().lower() in PAYMENT_PROCESSORS


def _same_payer(a, b, name_of) -> bool:
    """同じ相手として扱ってよいか。

    片方が決済代行のまま残っているなら、名前が違っても同じ取引でありうる。
    両方が決済代行のときは寄せない（別々の支払いを潰す）。
    """
    if name_of(a) == name_of(b):
        return True
    return is_payment_processor(name_of(a)) != is_payment_processor(name_of(b))


def collapse_duplicates(records: list, window_days: int = 3,
                        key=None) -> tuple[list, list[tuple]]:
    """同じ取引が複数の通知で届いたぶんを寄せる。残す側と、寄せた側を返す。

    「ご注文確認」と「発送済み」のように、1回の買い物が別々のメールで届く。
    送る側は進捗を知らせているだけなので通知は重複しておらず、Message-ID も
    本文も違う。**寄せる鍵は通知の側ではなく、その裏にある取引の側にしかない。**

    (請求元, 通貨, 金額) が一致し、日付が `window_days` 以内で、かつ
    `is_same_transaction` が通ったときだけ寄せる。金額と日付だけで寄せると、
    同じ額を何度も払う買い方を潰す。

    **それでも潰しうる。**同じ日に別の商品を同じ額で2回注文すれば、件名は違い、
    どちらにも「注文」が入る。だから消さずに返して、呼び出し側に必ず見せさせる。
    判定を強めるより、利用者が自分の買い方を知っている前提で外させるほうが安全側になる。

    寄せる先は**先に来たほう**にする。注文が先で発送が後なので、
    取引の日付としては注文日のほうが実態に近い。

    `records` は Record のリスト。日付は "YYYY-MM-DD" か "不明"。
    日付不明のものは寄せない(いつの取引か分からないものを同じとは言えない)。

    `key` は請求元を取り出す関数。既定は `merchant` だが、経費用途では
    作品名で上書きする前の `billed_by` を使う。**用途によって「同じ相手」の
    定義が違うので、寄せる側で決めさせる。**
    """
    name_of = key or (lambda r: r.merchant)
    kept: list = []
    dropped: list[tuple] = []
    # 先に来たほうへ寄せるので、日付順に見る。同日の並びは入力の順を保つ。
    for record in sorted(records, key=lambda r: (r.date == "不明", r.date)):
        match = None
        if record.date != "不明":
            for candidate in kept:
                if candidate.date == "不明":
                    continue
                if (candidate.currency, candidate.amount) != (
                    record.currency, record.amount
                ):
                    continue
                if not _same_payer(candidate, record, name_of):
                    continue
                if _days_between(candidate.date, record.date) > window_days:
                    continue
                # 決済代行が絡む組は、件名から進み具合が読めない
                # (「自動支払いを行いました」と "Subscription Confirmation")。
                # 相手を読めなかった通知であること自体が根拠になる。
                # 「どちらか一方だけ」が決済代行のときに限る。両方が決済代行なら、
                # 相手を読めなかった通知が2つ来ただけで、別々の支払いかもしれない。
                if is_payment_processor(name_of(candidate)) != is_payment_processor(name_of(record)):
                    if candidate.subject != record.subject:
                        match = candidate
                        break
                    continue
                if is_same_transaction(candidate, record):
                    match = candidate
                    break
        if match is not None:
            # 残すのは相手が分かっているほう。決済代行の記録が先に居たら入れ替える。
            # 「PayPal ¥610」より「鳴潮 ¥610」のほうが後から使える。
            if is_payment_processor(name_of(match)) and not is_payment_processor(name_of(record)):
                kept[kept.index(match)] = record
                dropped.append((match, record))
            else:
                dropped.append((record, match))
        else:
            kept.append(record)
    return kept, dropped


def _days_between(a: str, b: str) -> int:
    """"YYYY-MM-DD" 同士の日数差。読めない値は無限大扱いにして寄せない。"""
    try:
        return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)
    except ValueError:
        return 10**9


def _currency_of(pre: str | None, suf: str | None) -> str:
    for token in (pre, suf):
        if not token:
            continue
        t = token.strip()
        if t in CURRENCY_BY_TOKEN:
            return CURRENCY_BY_TOKEN[t]
        if t.upper() in CURRENCY_BY_TOKEN:
            return CURRENCY_BY_TOKEN[t.upper()]
    return DEFAULT_CURRENCY


def format_money(amount: Decimal, currency: str) -> str:
    symbol = CURRENCY_SYMBOL.get(currency, currency + " ")
    # 円は小数を出さない(元が整数のときだけ)
    if currency == "JPY" and amount == amount.to_integral_value():
        return f"{symbol}{int(amount):,}"
    return f"{symbol}{amount:,.2f}"


# 送信専用アドレスによく付く飾りのラベル。請求元名を推測するとき取り除く。
SENDER_NOISE = {
    "mail", "mails", "email", "emails", "e", "news", "info", "noreply", "no-reply",
    "reply", "mailer", "smtp", "notification", "notifications", "notice", "order",
    "orders", "shop", "store", "member", "service", "support", "account", "accounts",
    "post", "auto", "auto-confirm", "cs", "link", "click", "delivery", "receipt", "billing",
}

# jp の下にもう1段ある形式(co.jp / ne.jp など)を判別するため
TLD_SECOND = {"co", "ne", "or", "ac", "go", "gr", "ed", "lg", "com", "net", "org"}


def parse_from(from_header: str) -> tuple[str, str]:
    """From ヘッダから (表示名, ドメイン) を取り出す。

    例: '楽天市場 <order@rakuten.co.jp>' -> ('楽天市場', 'rakuten.co.jp')
    """
    if not from_header:
        return "", ""
    m = re.search(r"<([^>]+)>", from_header)
    address = (m.group(1) if m else from_header).strip()
    name = from_header[: m.start()].strip() if m else ""
    name = name.strip("\"' \t")
    domain = address.rsplit("@", 1)[1].lower().strip() if "@" in address else ""
    return name, domain


def merchant_from_domain(domain: str) -> str:
    """ドメインから請求元名を引き出す。email.apple.com -> Apple"""
    if not domain:
        return ""
    labels = [x for x in domain.split(".") if x]
    while len(labels) > 2 and labels[0] in SENDER_NOISE:
        labels.pop(0)
    if len(labels) < 2:
        return labels[0] if labels else ""
    # rakuten.co.jp なら co.jp を飛ばして rakuten を取る
    if len(labels[-1]) == 2 and labels[-2] in TLD_SECOND:
        idx = len(labels) - 3
    else:
        idx = len(labels) - 2
    core = labels[idx] if idx >= 0 else ""
    return core.capitalize() if core else ""


# 法人格を表す語。請求元名の末尾から落とす。
# 同じ相手が「COGNOSPHERE PTE. LTD.」「COGNOSPHERE PTE.LTD.」など
# 揺れて届くので、ここを揃えないと同一相手が別行に分かれる。
LEGAL_TOKENS = {
    "PTE", "LTD", "LIMITED", "INC", "LLC", "CORP", "CORPORATION",
    "CO", "GMBH", "SA", "BV", "KK", "PLC", "AG", "SRL",
}

LEGAL_PREFIX_JA = ("合同会社", "株式会社", "有限会社", "一般社団法人")

# 表示名がこれらだと請求元の役に立たない(実データに "no-reply <noreply@cygames.com>"
# があり、8件すべてが請求元「no-reply」になっていた)。ドメイン側に落とす。
NOISE_NAMES = {
    "noreply", "noreply2", "donotreply", "reply", "info", "mail", "email",
    "support", "service", "customerservice", "admin", "system", "postmaster",
    "notification", "notifications", "notice", "order", "orders", "billing",
}


def normalize_merchant(name: str) -> str:
    """請求元名の表記ゆれを吸収する。

    実データで起きていたこと:
      COGNOSPHERE PTE. LTD.                      -> COGNOSPHERE
      COGNOSPHERE PTE. LTD...                    -> COGNOSPHERE  (末尾が省略記号)
      COGNOSPHERE PTE.LTD.                       -> COGNOSPHERE  (Google Play 側の表記)
      HK KURO GAMES LIMITE...                    -> HK KURO GAMES(語の途中で切れている)
      KURO TECHNOLOGY (HONG KONG) CO., LIMITED   -> KURO TECHNOLOGY
      KURO TECHNOLOGY (HON...                    -> KURO TECHNOLOGY
      合同会社DMM.com info@mail.dmm.com +81 3-... -> DMM.com
    """
    if not name:
        return ""
    s = name.strip()

    # 連絡先が続けて入っていることがあるので、そこで切る
    s = re.split(r"\s+[\w.+-]+@[\w.-]+", s)[0]
    s = re.split(r"\s+\+\d", s)[0]

    truncated = bool(re.search(r"[.．]{2,}$", s))
    s = re.sub(r"[.．]{2,}$", "", s).strip()

    # 括弧が閉じていない = 途中で切れている。開き括弧の手前で捨てる
    if s.count("(") > s.count(")"):
        s = s[: s.rindex("(")].strip()

    for prefix in LEGAL_PREFIX_JA:
        if s.startswith(prefix):
            s = s[len(prefix):].strip()

    # 「法人格が始まる位置」で切る。括弧付きの地域表記もここで落ちる。
    tokens = s.split()
    cut = len(tokens)
    for i, token in enumerate(tokens):
        bare = token.strip(".,、()").upper()
        if not bare:
            continue
        if bare in LEGAL_TOKENS or token.startswith("("):
            cut = i
            break
        # 末尾が途中で切れた法人格(LIMITE -> LIMITED)
        if truncated and i == len(tokens) - 1 and len(bare) >= 2:
            if any(w.startswith(bare) for w in LEGAL_TOKENS):
                cut = i
                break
    s = " ".join(tokens[:cut]).strip() or s

    # "COGNOSPHERE PTE.LTD." のように空白無しで続く形
    s = re.sub(r"\s*(PTE|CO)\.?\s*(LTD|LIMITED)?\.?$", "", s, flags=re.IGNORECASE).strip()

    return s.rstrip(" .,、-").strip()


def identify_merchant(sender: str, subject: str) -> str:
    text = f"{sender} {subject}".lower()
    for merchant, keywords in MERCHANT_RULES.items():
        for kw in keywords:
            if kw.lower() in text:
                return merchant

    # ルールに無い相手でも「不明」で潰さない。
    # 送信元の表示名が一番読みやすく、無ければドメインから引き出す。
    name, domain = parse_from(sender)
    if name and "@" not in name and name.lower().replace("-", "").replace("_", "") not in NOISE_NAMES:
        return name[:30] + "…" if len(name) > 30 else name
    return merchant_from_domain(domain) or UNKNOWN_MERCHANT


def extract_amount(text: str) -> tuple[Decimal, str] | None:
    """(金額, 通貨コード) を返す。取れなければ None。

    通貨をここで確定させるのが要点。円とドルを混ぜて合計すると
    数字としては出るが意味が無いので、下流で必ず通貨ごとに分ける。

    **一致するたびに文脈を見て、支払額として読めないものは飛ばす。**
    最初の一致で打ち切ると、脚注に「1,000円以上で送料無料」と書いてある
    本物のレシートを、そのしきい値の金額で計上することになる。
    """
    if not text:
        return None
    for pattern in AMOUNT_PATTERNS:
        for match in pattern.finditer(text):
            if not is_paid_amount(text, match):
                continue
            raw = match.group("num").replace(",", "").strip()
            try:
                amount = Decimal(raw)
            except InvalidOperation:
                continue
            return amount, _currency_of(match.groupdict().get("pre"), match.groupdict().get("suf"))
    return None


# ---- 課金アイテム名からタイトルを特定する ----
#
# 決済上の請求元は運営会社なので、1社が複数タイトルを出していると
# 内訳が潰れる(実データでは COGNOSPHERE 1行に 451件が固まっていた)。
# 課金アイテム名はタイトルごとに固有なので、そこから引き当てる。
#
# 対応表なので、自分の遊んでいるタイトルに合わせて足すのが前提。
# 当たらなければ請求元のまま残る(勝手に推測して間違えるより良い)。
TITLE_BY_ITEM_KEYWORD: dict[str, tuple[str, ...]] = {
    "原神": ("創世結晶", "天空紀行", "空月の祝福", "原石"),
    "崩壊：スターレイル": ("列車補給標章", "星穹", "星軌通行証", "古櫃"),
    "ゼンレスゾーンゼロ": ("モノクローム", "インターノット会員権", "ナナシビトの褒章"),
    "鳴潮": ("月相", "潮汐", "波紋"),
    "崩壊3rd": ("水晶", "ギフトコイン"),
    "Fate/Grand Order": ("聖晶石", "呼符"),
    "プリンセスコネクト！Re:Dive": ("ジュエル",),
    "ドラゴンクエストウォーク": ("ジェムパック",),
}


def title_from_item(item: str) -> str:
    """課金アイテム名からタイトルを引く。分からなければ空。"""
    if not item:
        return ""
    for title, keywords in TITLE_BY_ITEM_KEYWORD.items():
        for kw in keywords:
            if kw in item:
                return title
    return ""


def canonicalize_merchants(names: list[str]) -> dict[str, str]:
    """表記ゆれを1つに寄せる対応表を作る。

    Apple の領収書はアプリ名に宣伝文が付くことがあり、同じアプリが
    複数の名前で届く(実データ412通で確認):

        ドラゴンクエストウォーク                        24件
        ドラゴンクエストウォーク ドラクエの位置情報ゲーム    8件
        ドラゴンクエストウォーク 歩く楽しみが増える位置情報ゲーム 4件

    短い名前が長い名前の先頭に一致するなら同じものとみなし、短いほうに寄せる。
    データから決めるので、アプリごとの対応表を持たなくて済む。

    短すぎる名前で巻き込むと別物まで統合するため、4文字未満は基準にしない。
    """
    uniq = sorted(set(n for n in names if n), key=len)
    mapping: dict[str, str] = {}
    for name in uniq:
        target = name
        for shorter in uniq:
            if len(shorter) < 4 or shorter == name:
                continue
            if len(shorter) >= len(name):
                break
            # 語の途中で切れた一致を避けるため、続きは区切り文字であること
            if not (name.startswith(shorter) and name[len(shorter)] in " 　-—:：/"):
                continue
            # 差分が短いものは別ブランドとみなして統合しない。
            # 宣伝文は長く(「 歩く楽しみが増える位置情報ゲーム」)、
            # 別サービスは短い(「Amazon」と「Amazon Pay」)という差を使う。
            # これが無いと Amazon Pay 経由の支払いが Amazon の買い物に混ざる。
            if len(name) - len(shorter) < 5:
                continue
            target = mapping.get(shorter, shorter)
            break
        mapping[name] = target
    return mapping


# ---- 請求元のカテゴリ分け ----
#
# 「いくら使ったか」は分類しないと判断に使えない。ソシャゲ課金を知りたいのに
# ふるさと納税や酒が同じ合計に入っていては意味がない。
#
# 完全な自動分類は不可能(同じ Apple でもゲーム課金とストレージ料金が混ざる)なので、
# 分かるものだけ内蔵し、残りは「未分類」に置く。
# ブラウザ版では画面上で変更でき、その設定はブラウザ内にだけ保存される。
UNCATEGORIZED = "未分類"

CATEGORY_RULES: dict[str, tuple[str, ...]] = {
    "ソシャゲ課金": (
        "COGNOSPHERE", "原神", "崩壊3rd", "崩壊：スターレイル", "ゼンレスゾーンゼロ",
        "鳴潮", "パニシング：グレイレイヴン", "ドラゴンクエストウォーク",
        "Fate/Grand Order", "プリンセスコネクト！Re:Dive",
        "KURO TECHNOLOGY", "HK KURO GAMES", "Cygames", "Gryph Frontier",
        "HaoPlay", "N2E ENTERTAINMENT", "miHoYo",
    ),
    "ゲーム": (
        "PlayStation", "Nintendo", "Steam", "Epic Games Commerce", "FINAL FANTASY",
        "英雄伝説", "ゲーム本編",
    ),
    "サブスク": (
        "iCloud", "Apple", "ファミリー (自動更新)", "YouTube", "Netflix", "Spotify",
    ),
    # 酒・成人向け配信など、生活必需ではない継続支出。買い物から分けると
    # 「減らせる支出」がそのまま1行で出る。
    "嗜好品": (
        "カクヤス", "DMM", "Dmm",
    ),
    # 制度上まったく別物なので買い物と混ぜない。控除の対象で、
    # 年をまたいで効く支出でもある。
    "ふるさと納税": (
        "さとふる", "ふるさとチョイス", "楽天ふるさと納税",
    ),
    "買い物": (
        "Amazon", "楽天市場", "ヨドバシ", "アニメイト",
        "どんぐり共和国", "ピクシブ", "BASE", "Aniplex", "Kfc",
        "モスバーガー", "珈琲問屋", "Tonya",
    ),
}


def category_of(merchant: str) -> str:
    """請求元からカテゴリを引く。前方一致と部分一致で拾い、外れたら未分類。"""
    if not merchant:
        return UNCATEGORIZED
    for category, names in CATEGORY_RULES.items():
        for name in names:
            if merchant == name or merchant.startswith(name) or name in merchant:
                return category
    return UNCATEGORIZED
