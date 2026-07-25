"""受取帳 CLI のエントリポイント。

    python3 -m receipt_ledger --source apple-mail --since 2026-01-01
    python3 -m receipt_ledger --source eml-dir --input-dir ./exported_mails
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analyze import Record, analyze
from .report import print_summary, write_detail_csv, write_summary_csv
from .rules import canonicalize_merchants
from .sources import SOURCES, AppleMailSource, EmlDirSource, SourceUnavailable


def build_source(args: argparse.Namespace):
    """--source の名前から実体を組み立てる。ソースを足すときはここに1分岐。"""
    if args.source == "apple-mail":
        return AppleMailSource(mail_dir=Path(args.mail_dir) if args.mail_dir else None)
    if args.source == "eml-dir":
        if not args.input_dir:
            sys.exit("エラー: --source eml-dir には --input-dir が必要です")
        return EmlDirSource(Path(args.input_dir))
    sys.exit(f"エラー: 未知のソース {args.source!r}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="receipt_ledger",
        description="レシートメールから請求元別に支出を集計する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="使えるソース:\n"
        + "\n".join(f"  {k:<12} {v}" for k, v in SOURCES.items()),
    )
    p.add_argument("--source", default="apple-mail", choices=list(SOURCES),
                   help="メールの取得元 (既定: apple-mail)")
    p.add_argument("--input-dir", help="--source eml-dir のときの .eml フォルダ")
    p.add_argument("--mail-dir", help="Mail.app のデータ位置を上書きする(検証用)")
    p.add_argument("--since", metavar="YYYY-MM-DD", help="この日以降のメールだけ対象にする")
    p.add_argument("--until", metavar="YYYY-MM-DD", help="この日以前のメールだけ対象にする")
    p.add_argument("--output", default="summary.csv", help="集計CSVの出力先")
    p.add_argument("--detail-output", help="明細CSVも出す場合のパス")
    p.add_argument("--quiet", action="store_true", help="進捗表示を出さない")
    return p.parse_args(argv)


def collect(source, args) -> tuple[list[Record], int, int, int, int]:
    records: list[Record] = []
    scanned = 0
    skipped = 0
    filtered = 0
    duplicated = 0
    seen: set[str] = set()

    for message in source.iter_messages():
        scanned += 1
        if not args.quiet and scanned % 500 == 0:
            print(f"  走査中… {scanned} 通", end="\r", file=sys.stderr)

        if message.uid in seen:
            continue
        seen.add(message.uid)

        record = analyze(message)
        if record is None:
            skipped += 1
            continue

        # 同じメールが別のパスから2回来ることがある。
        # 書き出しフォルダが重なっていたり、同じ範囲を二度書き出した場合など。
        # パスだけで見ていると二重計上されるので Message-ID でも弾く。
        if record.message_id:
            if record.message_id in seen:
                duplicated += 1
                continue
            seen.add(record.message_id)
        # 日付での絞り込みは Date ヘッダを見てから。日付不明のものは落とさない。
        if record.date != "不明":
            if (args.since and record.date < args.since) or (
                args.until and record.date > args.until
            ):
                filtered += 1
                continue
        records.append(record)

    if not args.quiet and scanned >= 500:
        print(" " * 40, end="\r", file=sys.stderr)

    # 同じ相手が複数の表記で届くので、集計前に寄せる
    alias = canonicalize_merchants([r.merchant for r in records])
    for r in records:
        r.merchant = alias.get(r.merchant, r.merchant)

    return records, scanned, skipped, filtered, duplicated


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = build_source(args)

    try:
        source.check()
    except SourceUnavailable as e:
        print(f"エラー: {e}", file=sys.stderr)
        if e.hint:
            print(f"\n{e.hint}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"ソース: {source.name}", file=sys.stderr)

    records, scanned, skipped, filtered, duplicated = collect(source, args)

    if not records:
        print("\n金額を抽出できたメールがありませんでした。", file=sys.stderr)
        if scanned == 0:
            print("メールが1通も見つかっていません。ソースの指定を確認してください。", file=sys.stderr)
        else:
            print(f"{scanned} 通を走査しましたが該当なしです。"
                  " rules.py の MERCHANT_RULES / AMOUNT_PATTERNS の調整が要るかもしれません。",
                  file=sys.stderr)
        return 0

    write_summary_csv(Path(args.output), records)
    if args.detail_output:
        write_detail_csv(Path(args.detail_output), records)

    if not args.quiet:
        print_summary(records, scanned, skipped, filtered, duplicated)
        print(f"\n-> {args.output}", file=sys.stderr)
        if args.detail_output:
            print(f"-> {args.detail_output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
