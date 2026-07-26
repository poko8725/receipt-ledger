"""受取帳 CLI のエントリポイント。

    python3 -m receipt_ledger --source apple-mail --since 2026-01-01
    python3 -m receipt_ledger --source eml-dir --input-dir ./exported_mails
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .analyze import Record, UnsupportedFormat, analyze
from .console import enable_utf8_output
from .report import print_summary, write_detail_csv, write_summary_csv
from .rules import canonicalize_merchants
from .sources import SOURCES, AppleMailSource, EmlDirSource, ImapSource, SourceUnavailable


def ymd(value: str) -> str:
    """--since / --until の書式を受け取った時点で検証する。

    ここを素通しすると、下流の文字列比較が静かに壊れる。
    "2026-07-24" < "2026/01/01" は ASCII で '-' < '/' なので True になり、
    **全件が「期間より前」と判定されて 0 件になる**。しかも例外が出ないので、
    利用者は解析ルールのほうを疑うことになる。
    """
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"日付は YYYY-MM-DD で指定してください（受け取った値: {value}）"
        ) from None
    return value


def build_source(args: argparse.Namespace):
    """--source の名前から実体を組み立てる。ソースを足すときはここに1分岐。"""
    if args.source == "apple-mail":
        return AppleMailSource(mail_dir=Path(args.mail_dir) if args.mail_dir else None)
    if args.source == "imap":
        return ImapSource(folder=args.imap_folder, since=args.since)
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
    p.add_argument("--imap-folder", default="INBOX",
                   help="IMAP のフォルダ名。--list-folders の出力をそのまま渡す")
    p.add_argument("--list-folders", action="store_true",
                   help="IMAP のフォルダ一覧を出して終わる")
    p.add_argument("--source", default="apple-mail", choices=list(SOURCES),
                   help="メールの取得元 (既定: apple-mail)")
    p.add_argument("--input-dir", help="--source eml-dir のときの .eml フォルダ")
    p.add_argument("--mail-dir", help="Mail.app のデータ位置を上書きする(検証用)")
    p.add_argument("--since", type=ymd, metavar="YYYY-MM-DD", help="この日以降のメールだけ対象にする")
    p.add_argument("--until", type=ymd, metavar="YYYY-MM-DD", help="この日以前のメールだけ対象にする")
    p.add_argument("--output", default="summary.csv", help="集計CSVの出力先")
    p.add_argument("--detail-output", help="明細CSVも出す場合のパス")
    p.add_argument("--quiet", action="store_true", help="進捗表示を出さない")
    return p.parse_args(argv)


def collect(source, args) -> tuple[list[Record], int, int, int, int]:
    records: list[Record] = []
    scanned = 0
    unsupported: list[str] = []
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

        try:
            record = analyze(message)
        except UnsupportedFormat as e:
            # 1件で止めない。読めなかったことは最後にまとめて知らせる。
            unsupported.append(str(e))
            continue
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

    if unsupported:
        # 黙って件数だけ減らすと、利用者は「足りない」理由に辿り着けない。
        print(f"\n読めない形式が {len(unsupported)} 件ありました:", file=sys.stderr)
        for line in unsupported[:5]:
            print(f"  {line.splitlines()[0]}", file=sys.stderr)
        if len(unsupported) > 5:
            print(f"  ... 他 {len(unsupported) - 5} 件", file=sys.stderr)

    return records, scanned, skipped, filtered, duplicated


def main(argv: list[str] | None = None) -> int:
    # Windows の cp932 コンソールでは金額の ¥ が出力できず、表示で落ちる。
    enable_utf8_output()
    args = parse_args(argv)
    source = build_source(args)

    try:
        source.check()
    except SourceUnavailable as e:
        print(f"エラー: {e}", file=sys.stderr)
        if e.hint:
            print(f"\n{e.hint}", file=sys.stderr)
        return 1

    if args.list_folders:
        if not isinstance(source, ImapSource):
            sys.exit("--list-folders は --source imap のときだけ使えます")
        for name in source.list_folders():
            print(name)
        source.close()
        return 0

    if not args.quiet:
        print(f"ソース: {source.name}", file=sys.stderr)

    try:
        records, scanned, skipped, filtered, duplicated = collect(source, args)
    except SourceUnavailable as e:
        # check() だけでなく、走査中にも SourceUnavailable は出る
        # (フォルダ名が違うなど、接続してみないと分からないもの)。
        # ここで捕まえないと、せっかく書いた案内が出ずにトレースバックだけが見える。
        print(f"エラー: {e}", file=sys.stderr)
        hint = getattr(e, "hint", None)
        if hint:
            print(f"\n{hint}", file=sys.stderr)
        return 1
    finally:
        # IMAP は同時接続数に上限がある(Gmail は 15)。logout せずに繰り返すと詰まる。
        close = getattr(source, "close", None)
        if callable(close):
            close()

    if not records:
        print("\n金額を抽出できたメールがありませんでした。", file=sys.stderr)
        if scanned == 0:
            print("メールが1通も見つかっていません。ソースの指定を確認してください。", file=sys.stderr)
        elif filtered:
            # 期間で落ちたぶんが多いのに解析ルールを疑わせると、
            # 利用者は関係のない場所を探すことになる。原因の候補を正しい順に出す。
            print(f"{scanned} 通を走査し、{filtered} 通が期間の指定で除外されました。"
                  f"\n  --since / --until を外すか、範囲を広げて試してください。",
                  file=sys.stderr)
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
