"""日付のタイムゾーン差が、実際に何件に効いているかを数える。

ブラウザ版は Date ヘッダを UTC に直して日付にしている(`toISOString()`)。
CLI 版はメール自身のタイムゾーンのまま日付にしている。
JST なら 00:00〜08:59 に届いたメールで、両者の日付が1日ずれる。

問題は「ずれること」ではなく、**集計の単位である月や年をまたぐか**である。
直すべきかどうかを、印象ではなくこの数で決める。

    cd ~/projects/receipt-ledger
    python3 compare/tz_impact.py                          # Mail.app を直接読む
    python3 compare/tz_impact.py --input-dir ~/Desktop/eml   # 書き出し済みフォルダ

出力するのは件数だけ。件名・金額・請求元は一切表示しない。
"""

from __future__ import annotations

import argparse
import sys
from datetime import timezone
from email import message_from_bytes
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "cli"))

from receipt_ledger.analyze import analyze  # noqa: E402
from receipt_ledger.console import enable_utf8_output  # noqa: E402
from receipt_ledger.rules import category_of  # noqa: E402
from receipt_ledger.sources import AppleMailSource, EmlDirSource, SourceUnavailable  # noqa: E402


def main() -> None:
    enable_utf8_output()
    ap = argparse.ArgumentParser(description="日付のタイムゾーン差の影響件数を数える")
    ap.add_argument("--input-dir", help="書き出し済みの .eml が入ったフォルダ")
    ap.add_argument("--category", help="このカテゴリだけに絞る（例: ソシャゲ課金）")
    ap.add_argument("--year-table", action="store_true",
                    help="年別の集計を新旧の解釈で並べ、差が出る年を示す")
    args = ap.parse_args()

    source = EmlDirSource(Path(args.input_dir)) if args.input_dir else AppleMailSource()
    try:
        source.check()
    except SourceUnavailable as e:
        sys.exit(str(e))

    total = 0          # レシートとして数えられたメール
    shifted = 0        # 日付が1日ずれる
    month_shift = 0    # そのうち月が変わる
    year_shift = 0     # そのうち年が変わる
    seen: set[str] = set()
    # 年別集計を2通りの解釈で持つ。{年: [件数, 金額]}
    by_year_local: dict[int, list] = {}
    by_year_utc: dict[int, list] = {}

    for message in source.iter_messages():
        record = analyze(message)
        if record is None:
            continue
        if record.message_id:
            if record.message_id in seen:
                continue
            seen.add(record.message_id)
        if args.category and category_of(record.merchant) != args.category:
            continue
        if record.currency != "JPY":
            # 通貨をまたいで足さない。年別表は円建てだけで見る。
            continue

        try:
            dt = parsedate_to_datetime(message_from_bytes(message.raw).get("Date", ""))
        except (TypeError, ValueError):
            continue
        if dt is None:
            continue

        local = dt.date()                                  # 修正後(メールのタイムゾーン)
        utc = dt.astimezone(timezone.utc).date()           # 修正前(ブラウザ版が使っていた UTC)
        total += 1

        for table, day in ((by_year_local, local), (by_year_utc, utc)):
            slot = table.setdefault(day.year, [0, 0])
            slot[0] += 1
            slot[1] += int(record.amount)

        if local == utc:
            continue
        shifted += 1
        if (local.year, local.month) != (utc.year, utc.month):
            month_shift += 1
        if local.year != utc.year:
            year_shift += 1

    print(f"対象メール           {total:>6} 通")
    print(f"日付がずれる         {shifted:>6} 通  ({pct(shifted, total)})")
    print(f"  うち月をまたぐ     {month_shift:>6} 通  ({pct(month_shift, total)})")
    print(f"  うち年をまたぐ     {year_shift:>6} 通  ({pct(year_shift, total)})")
    print()
    if month_shift == 0:
        print("月別集計への影響なし。表示上の日付だけの問題。")
    else:
        print("月別集計が変わる。")

    if not args.year_table:
        return

    print()
    label = args.category or "全カテゴリ"
    print(f"年別（{label} / JPY のみ）")
    print(f"{'年':<6}{'修正前(UTC)':>22}{'修正後':>22}   差")
    changed = 0
    for year in sorted(set(by_year_local) | set(by_year_utc)):
        old_n, old_v = by_year_utc.get(year, [0, 0])
        new_n, new_v = by_year_local.get(year, [0, 0])
        mark = ""
        if (old_n, old_v) != (new_n, new_v):
            changed += 1
            mark = f"   件数 {new_n - old_n:+d} / 金額 {new_v - old_v:+,}"
        print(f"{year:<6}{f'¥{old_v:,} {old_n}件':>22}{f'¥{new_v:,} {new_n}件':>22}{mark}")

    print()
    if changed == 0:
        print("2つの解釈で年別表は一致する。")
    else:
        print(f"{changed} 個の年で値が変わる。公開済みの数字がどちらの解釈で出たものかを確認する。")


def pct(part: int, whole: int) -> str:
    return "0.0%" if whole == 0 else f"{part / whole * 100:.1f}%"


if __name__ == "__main__":
    main()
