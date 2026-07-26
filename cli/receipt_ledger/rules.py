"""請求元判定と金額抽出のルール。

CLI 側の正。ここを直せば全ソース(Mail.app / .eml / 将来の Gmail)に効く。

注意: index.html にも同じ内容の JS 版がある。ブラウザ版はビルド無しの
HTML 1枚を維持しているため、現状は手で同期させる必要がある。
片方だけ直すと挙動がずれるので、ルールを足すときは必ず両方を見ること。
"""

from __future__ import annotations

import re
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
AMOUNT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        # 読み飛ばしは最短一致にする。貪欲だと "Total: USD 49.99" の USD を
        # 食い潰して通貨不明(=既定の円)に化ける。
        rf"{_LABEL}[^\d¥￥$€£]{{0,12}}?(?P<pre>{_PRE})?\s*(?P<num>{_NUM})\s*(?P<suf>{_SUF})?",
        re.IGNORECASE,
    ),
    re.compile(rf"(?P<pre>{_PRE})\s*(?P<num>{_NUM})", re.IGNORECASE),
    re.compile(rf"(?P<num>{_NUM})\s*(?P<suf>{_SUF})", re.IGNORECASE),
]


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
    """
    if not text:
        return None
    for pattern in AMOUNT_PATTERNS:
        match = pattern.search(text)
        if not match:
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
        "iCloud", "Apple Music", "ファミリー (自動更新)", "YouTube", "Netflix", "Spotify",
    ),
    "買い物": (
        "Amazon", "楽天市場", "ヨドバシ", "さとふる", "カクヤス", "アニメイト",
        "どんぐり共和国", "ピクシブ", "BASE", "Aniplex", "DMM", "Kfc",
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
