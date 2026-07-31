"""集計と出力。"""

from __future__ import annotations

import csv
import sys
import unicodedata
from collections import defaultdict
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .analyze import Record
from .rules import category_of, format_money

# 表計算ソフトは先頭が = + - @ のセルを数式として解釈する。
# 請求元名も品目もメール由来＝第三者が送り込める値なので、
# そのまま書くと「CSV を開いただけで数式が走る」状態になる。
# この道具は「Excel で開く」ことを前提にしているので、直撃する。
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """数式として解釈されうるセルの先頭に ' を付ける。

    Excel は先頭の ' を「以降は文字列」の印として扱い、表示もしない。
    値そのものは壊さずに、実行だけを止められる。
    """
    if isinstance(value, str) and value.startswith(_FORMULA_LEAD):
        return "'" + value
    return value


class _SafeWriter:
    """全セルを csv_safe に通してから書く。

    呼び出し側で1セルずつ csv_safe を書く形にしていたところ、
    write_detail_csv で掛け忘れていて件名がそのまま出ていた。件名は
    第三者が自由に決められるので、そこが一番危ない列だった。

    ブラウザ版(index.html の toCsv)は全セルを通す関所になっていて
    漏れていない。同じ形にして、書き出しが増えても掛け忘れが
    起きないようにする。
    """

    def __init__(self, f) -> None:
        self._w = csv.writer(f)

    def writerow(self, row: Iterable) -> None:
        self._w.writerow([csv_safe(v) for v in row])


@contextmanager
def _open_csv(path: Path):
    """CSV を書き出す唯一の入口。

    Excel が UTF-8 と認識できるよう BOM 付きにする。
    """
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        yield _SafeWriter(f)


def totals_by_merchant(
    records: Iterable[Record],
) -> tuple[dict[tuple[str, str], Decimal], dict[tuple[str, str], int]]:
    """(請求元, 通貨) 単位で集計する。

    キーに通貨を含めているのは、円とドルを足した数字を作らせないため。
    合算してしまうと数字は出るが意味が無い。
    """
    totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for r in records:
        key = (r.merchant, r.currency)
        totals[key] += r.amount
        counts[key] += 1
    return dict(totals), dict(counts)


def totals_by_category(
    records: Iterable[Record],
) -> tuple[dict[tuple[str, str], Decimal], dict[tuple[str, str], int]]:
    """(カテゴリ, 通貨) 単位で集計する。

    合計は分類しないと判断に使えない。請求元別の内訳より、
    まずこちらを見たい場面のほうが多い。
    """
    totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for r in records:
        key = (category_of(r.merchant), r.currency)
        totals[key] += r.amount
        counts[key] += 1
    return dict(totals), dict(counts)


def grand_totals(records: Iterable[Record]) -> dict[str, Decimal]:
    """通貨ごとの総額。1つの数字にはまとめない。"""
    out: dict[str, Decimal] = defaultdict(Decimal)
    for r in records:
        out[r.currency] += r.amount
    return dict(out)


def write_summary_csv(path: Path, records: list[Record]) -> None:
    totals, counts = totals_by_merchant(records)
    with _open_csv(path) as w:
        w.writerow(["カテゴリ", "請求元", "通貨", "合計金額", "件数", "平均単価"])
        for (merchant, currency) in sorted(totals, key=lambda k: (k[1], -totals[k])):
            total, count = totals[(merchant, currency)], counts[(merchant, currency)]
            w.writerow([category_of(merchant), merchant, currency, total, count,
                        (total / count).quantize(total)])


def write_detail_csv(path: Path, records: list[Record]) -> None:
    header = ["日付", "カテゴリ", "請求元", "品目", "通貨", "金額", "送信元", "件名", "メールボックス", "出所"]
    fields = ["date", "merchant", "item", "currency", "amount", "sender", "subject", "mailbox", "origin"]
    with _open_csv(path) as w:
        w.writerow(header)
        for r in sorted(records, key=lambda x: x.date):
            row = [getattr(r, k) for k in fields]
            w.writerow([row[0], category_of(r.merchant)] + row[1:])


def _width(s: str) -> int:
    """全角を2桁として数える(等幅端末で列を揃えるため)。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, width: int) -> str:
    """左寄せ。str.ljust は全角を1桁と数えるので使えない。"""
    return s + " " * max(0, width - _width(s))


def _rpad(s: str, width: int) -> str:
    """右寄せ。同上の理由で str.rjust は使えない。"""
    return " " * max(0, width - _width(s)) + s


def print_summary(records: list[Record], scanned: int, skipped: int,
                  filtered: int = 0, duplicated: int = 0) -> None:
    totals, counts = totals_by_merchant(records)
    grands = grand_totals(records)

    summary = " / ".join(format_money(v, c) for c, v in sorted(grands.items()))
    print(f"\n走査 {scanned} 通 / レシート {len(records)} 件 / 総額 {summary}", file=sys.stderr)
    if skipped:
        print(f"(金額を抽出できなかった {skipped} 通はスキップ)", file=sys.stderr)
    if duplicated:
        print(f"(同じメールが重複していた {duplicated} 件を除外)", file=sys.stderr)
    if filtered:
        print(f"(期間指定の対象外だった {filtered} 件を除外)", file=sys.stderr)
    if len(grands) > 1:
        print("(通貨が混在しています。通貨をまたぐ合計は出しません)", file=sys.stderr)
    print("", file=sys.stderr)

    # 請求元別より先にカテゴリ別を出す。判断に使うのはこちらのため。
    cat_totals, cat_counts = totals_by_category(records)
    if len(set(c for c, _ in cat_totals)) > 1:
        cat_w = max([_width(c) for c, _ in cat_totals] + [_width("カテゴリ")])
        print(f"{_pad('カテゴリ', cat_w)}  {_rpad('合計金額', 14)}  {_rpad('件数', 6)}", file=sys.stderr)
        print("-" * (cat_w + 24), file=sys.stderr)
        for key in sorted(cat_totals, key=lambda k: (k[1], -cat_totals[k])):
            print(f"{_pad(key[0], cat_w)}  {_rpad(format_money(cat_totals[key], key[1]), 14)}  "
                  f"{_rpad(str(cat_counts[key]), 6)}", file=sys.stderr)
        print("", file=sys.stderr)

    name_w = max([_width(m) for m, _ in totals] + [_width("請求元")])
    amount_w, count_w = 14, 6

    # 通貨ごとに区切って出す
    for currency in sorted({c for _, c in totals}):
        rows = {k: v for k, v in totals.items() if k[1] == currency}
        if len(grands) > 1:
            print(f"[{currency}]", file=sys.stderr)
        print(f"{_pad('請求元', name_w)}  {_rpad('合計金額', amount_w)}  {_rpad('件数', count_w)}",
              file=sys.stderr)
        print("-" * (name_w + amount_w + count_w + 4), file=sys.stderr)
        for key in sorted(rows, key=lambda k: -rows[k]):
            amount = format_money(rows[key], currency)
            print(f"{_pad(key[0], name_w)}  {_rpad(amount, amount_w)}  "
                  f"{_rpad(str(counts[key]), count_w)}", file=sys.stderr)
        print("", file=sys.stderr)
