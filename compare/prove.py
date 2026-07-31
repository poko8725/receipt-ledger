"""照合装置が本当に検知できることを、欠陥を戻して確かめる。

    python3 compare/prove.py

## なぜ要るのか

欠陥を直したあと、`compare.py` は常に「0 件が食い違い」を返すようになる。
このとき、**正しく検査している装置と、何も検査していない装置は出力で区別が付かない**。
切り出し範囲がずれてコードが欠けても、フィクスチャがパーサーに届いていなくても、
表示は同じ「一致」になる。

そこで、過去に実際にあった欠陥をブラウザ側のコードに戻し、
**どのフィクスチャが差分に転ぶか**を見る。1つも転ばない欠陥があれば、
その欠陥は今の入力では検知できない、という穴の在処が分かる。

Python 側には手を入れない。両方を可変にすると、仕掛けそのものが
新しいバグの置き場になる。ここで確かめたいのは「入力が効いているか」なので、
片側を固定した正解として使えば足りる。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_js  # noqa: E402
import run_py  # noqa: E402
from compare import diff_case  # noqa: E402

sys.path.insert(0, str(HERE.parent / "cli"))
from receipt_ledger.console import enable_utf8_output  # noqa: E402

# (名前, 説明, 置換前, 置換後)
# 置換前が見つからなければその場で止める。コードが動いたのに気づかず
# 「検知できません」と報告するほうが害が大きい。
DEFECTS = [
    (
        "文字コードを復号しない",
        "7bit/8bit の本文をバイト列のまま扱う",
        "  return decodeBinaryString(text, charset);",
        "  return text;",
    ),
    (
        "実体参照を解決しない",
        "&yen; などをそのまま残す",
        "function decodeEntities(text) {",
        "function decodeEntities(text) { return text;",
    ),
    (
        "タグを空白にせず削除する",
        "表のセルが区切りなしで連結される",
        'text = text.replace(/<[^>]+>/g, " ");',
        'text = text.replace(/<[^>]+>/g, "");',
    ),
    (
        "分割語の空白を詰めない",
        "RFC 2047 の encoded-word の間の空白を本文として残す",
        'str = str.replace(/\\?=[ \\t]*(?:\\r?\\n[ \\t]*)?=\\?/g, "?==?");',
        "",
    ),
    (
        "数式をエスケープしない",
        "CSV に出る直前の値をそのまま通す(開いただけで数式が走る)",
        '  return /^[=+\\-@\\t\\r]/.test(s) ? "\'" + s : s;',
        "  return s;",
    ),
    (
        "代理決済の本文を読まない",
        "送信元だけで請求元を判定する",
        "  const receipt = parseReceipt(sender, subject, body);",
        "  const receipt = null;",
    ),
    (
        "multipart で html を優先する",
        "text/plain があっても html 側を読む",
        "    if (plain) bodyText = plain.text;\n"
        "    else if (html) bodyText = htmlToText(html.text);",
        "    if (html) bodyText = htmlToText(html.text);\n"
        "    else if (plain) bodyText = plain.text;",
    ),
    # 日付まわりの2つは、手元のタイムゾーンによって効くフィクスチャが入れ替わる。
    # 両実装とも「読む人の暦」で日付にするので、UTC の機械で回すと
    # 「UTC に寄せる」は変化を起こせず見逃しになる。これは入力の不足ではなく、
    # その機械では区別のしようがないためで、フィクスチャを足しても埋まらない。
    (
        "日付を UTC に寄せる",
        "読む人の暦ではなく UTC で日付にする",
        "function dateOnlyFromHeader(dateStr) {",
        "function dateOnlyFromHeader(dateStr) {\n"
        "  { const _d = parseDateHeader(dateStr);\n"
        "    return _d ? _d.toISOString().slice(0, 10) : null; }",
    ),
    (
        "書かれたオフセットのままの日付にする",
        "海外から届いた領収書が、手元の暦より1日前になる",
        "function dateOnlyFromHeader(dateStr) {",
        "function dateOnlyFromHeader(dateStr) {\n"
        "  { const _d = parseDateHeader(dateStr);\n"
        "    if (!_d) return null;\n"
        "    const _m = /([+-])(\\d{2})(\\d{2})\\s*$/.exec(String(dateStr).trim());\n"
        "    if (_m) {\n"
        "      const _o = (_m[1] === '-' ? -1 : 1) * (Number(_m[2]) * 60 + Number(_m[3]));\n"
        "      return new Date(_d.getTime() + _o * 60000).toISOString().slice(0, 10);\n"
        "    } }",
    ),
]


def run_browser(core: str) -> dict:
    """変異させたコードでブラウザ側を1回まわす。"""
    page = run_js.build_page(core)
    import json
    import re
    import base64
    import tempfile

    chrome = run_js.find_chrome()
    # ignore_cleanup_errors の理由は run_js.py と同じ。
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        html = Path(tmp) / "harness.html"
        html.write_text(page, encoding="utf-8")
        dumped = run_js.read_dom([
            chrome, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions",
            f"--user-data-dir={tmp}/profile", "--dump-dom", html.as_uri(),
        ])
    match = re.search(r"<body>([A-Za-z0-9+/=\s]*)</body>", dumped)
    if not match or not match.group(1).strip():
        sys.exit("ブラウザから結果を取り出せませんでした")
    return json.loads(base64.b64decode(match.group(1).strip()).decode("utf-8"))


def main() -> None:
    enable_utf8_output()
    core = run_js.extract_core()
    truth = run_py.run()

    print("欠陥を戻して、どのフィクスチャが落ちるかを見る")
    print()
    blind = []
    for name, why, old, new in DEFECTS:
        if old not in core:
            sys.exit(
                f"欠陥『{name}』を注入できません。対象のコードが見つかりませんでした。\n"
                f"  探した文字列: {old!r}\n"
                "  index.html を変更したなら、prove.py の DEFECTS も直してください。"
            )
        broken = run_browser(core.replace(old, new, 1))
        caught = [
            fixture for fixture in sorted(truth)
            if diff_case(broken.get(fixture), truth.get(fixture))
        ]
        mark = "検知" if caught else "見逃し"
        print(f"[{mark}] {name} - {why}")
        for fixture in caught:
            print(f"           {fixture}")
        if not caught:
            blind.append(name)
        print()

    if blind:
        print(f"{len(blind)} 個の欠陥を、今の入力では検知できない:")
        for name in blind:
            print(f"  - {name}")
        print("この欠陥を踏ませるフィクスチャを足すか、検知できない理由を書き残す。")
        sys.exit(1)

    print(f"{len(DEFECTS)} 個すべてを検知した。装置は生きている。")


if __name__ == "__main__":
    main()
