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
from .rules import canonicalize_merchants, collapse_duplicates, format_money
from .sources import SOURCES, AppleMailSource, EmlDirSource, ImapSource, SourceUnavailable
from .sources.apple_mail import FDA_HINT, is_excluded


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
        return AppleMailSource(
            mail_dir=Path(args.mail_dir) if args.mail_dir else None,
            include_excluded=args.all_mailboxes,
            mailboxes=args.mailbox,
        )
    if args.source == "imap":
        return ImapSource(folder=args.imap_folder, since=args.since)
    if args.source == "eml-dir":
        if not args.input_dir:
            sys.exit("エラー: --source eml-dir には --input-dir が必要です")
        return EmlDirSource(Path(args.input_dir))
    sys.exit(f"エラー: 未知のソース {args.source!r}")


def print_mailboxes(counts: dict[str, int]) -> None:
    """メールボックス一覧。**通数を必ず添える。**

    0 通のものが一覧に混ざっているのが分かるようにする。Gmail のラベルは
    Mail.app 上では他と同じに見えるのに、ローカルには実体が無いことがあり、
    名前だけ並べると「指定できるはず」と読めてしまう。
    """
    for name in sorted(counts):
        note = "  (既定では除外)" if is_excluded(name) else ""
        if not counts[name]:
            note += "  ← ローカルに実体なし"
        print(f"{name}  {counts[name]} 通{note}")


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
    p.add_argument("--mailbox", action="append", metavar="名前",
                   help="このメールボックスだけ集計する。複数回指定できる。"
                        "名前は --list-mailboxes の出力をそのまま渡す")
    p.add_argument("--list-mailboxes", action="store_true",
                   help="Mail.app のメールボックス一覧を出して終わる")
    p.add_argument("--all-mailboxes", action="store_true",
                   help="既定で除外しているメールボックス(ゴミ箱・迷惑メール・下書き・送信済み)も読む")
    p.add_argument("--since", type=ymd, metavar="YYYY-MM-DD", help="この日以降のメールだけ対象にする")
    p.add_argument("--until", type=ymd, metavar="YYYY-MM-DD", help="この日以前のメールだけ対象にする")
    p.add_argument("--output", default="summary.csv", help="集計CSVの出力先")
    p.add_argument("--detail-output", help="明細CSVも出す場合のパス")
    p.add_argument("--quiet", action="store_true", help="進捗表示を出さない")
    p.add_argument("--keep-duplicates", action="store_true",
                   help="同じ取引が複数の通知で届いたぶんも、そのまま数える")
    p.add_argument("--duplicate-window", type=int, default=3, metavar="日数",
                   help="同じ請求元・同じ金額を同じ取引とみなす日数の幅 (既定 3)")
    return p.parse_args(argv)


def print_duplicates(dropped: list[tuple]) -> None:
    """寄せたぶんを必ず出す。**黙って消すと、正しい額との差の理由が消える。**

    同額の買い物を本当に2回した人を潰しうる操作なので、
    利用者が「これは別の買い物だ」と気づける形にしておく。
    """
    if not dropped:
        return
    print(f"\n同じ取引とみなして {len(dropped)} 件を寄せました "
          f"(--keep-duplicates で寄せずに数えます):", file=sys.stderr)
    for record, kept in dropped[:20]:
        print(f"  {record.date}  {record.merchant}  "
              f"{format_money(record.amount, record.currency)}  «{record.subject[:32]}»",
              file=sys.stderr)
        print(f"  {'':12}  ↑ {kept.date} の «{kept.subject[:32]}» と同じ取引とみなしました",
              file=sys.stderr)
    if len(dropped) > 20:
        print(f"  ... 他 {len(dropped) - 20} 件", file=sys.stderr)


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
        # record.date は手元のタイムゾーンに直した日付なので、--since / --until に
        # 書いた日付と同じ暦で比べられる(analyze.local_datetime を参照)。
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

    # 表記を寄せたあとで取引を寄せる。順序が逆だと、同じ相手の別表記が
    # 別の請求元に見えて、同じ取引だと分からない。
    if not args.keep_duplicates:
        records, dropped = collapse_duplicates(records, args.duplicate_window)
        print_duplicates(dropped)

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

    if args.list_mailboxes:
        if not isinstance(source, AppleMailSource):
            sys.exit("--list-mailboxes は --source apple-mail のときだけ使えます")
        print_mailboxes(source.mailbox_counts())
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
            if isinstance(source, AppleMailSource):
                # 権限が無くても走査は例外を投げず、0 通として終わる。
                # ここで案内しないと「今年は領収書が無かった」と読み違える。
                print("\nフルディスクアクセスが無いと、エラーではなく 0 通として出ます。",
                      file=sys.stderr)
                print(FDA_HINT, file=sys.stderr)
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
