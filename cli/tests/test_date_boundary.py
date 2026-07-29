"""Date ヘッダのタイムゾーンと、期間指定の境界。

    cd cli && python3 -m unittest discover tests

ここで見ているのは1つだけ:

    **利用者が --since / --until に書く日付と、レシートに付く日付が
    同じ暦の上にあるか。**

海外の事業者からの領収書は `-0700` のようなオフセットで届く。書かれた
ままの日付で期間を判定すると、手元の暦では範囲に入っているメールが
静かに落ちる。落ちても例外は出ず、合計が小さくなるだけなので、
テストが無ければ気づけない。

実際に落ちた形をそのまま入力にしてある(-0700 の 7/15 10:43〜10:59 =
JST の 7/16 02:43〜02:59、PayPal の領収書6通、計 ¥3,780)。
"""

from __future__ import annotations

import csv
import email
import io
import os
import sys
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from receipt_ledger.__main__ import main  # noqa: E402
from receipt_ledger.analyze import analyze, local_datetime  # noqa: E402
from receipt_ledger.sources.base import RawMessage  # noqa: E402
from receipt_ledger.sources.imap import imap_date  # noqa: E402


def eml(date_header: str, *, amount: int = 630, ident: str = "1") -> bytes:
    """最小のレシートメール。見たいのは Date だけなので他は固定する。"""
    return (
        "From: service@paypal.co.jp\r\n"
        "To: user@example.com\r\n"
        "Subject: PayPal\r\n"
        f"Date: {date_header}\r\n"
        f"Message-ID: <boundary-{ident}@example.invalid>\r\n"
        "Content-Type: text/plain; charset=UTF-8\r\n"
        "\r\n"
        f"合計 ¥{amount:,}\r\n"
    ).encode("utf-8")


def date_of(date_header: str) -> str:
    """Date ヘッダ1つを、レコードの日付にするまで。"""
    record = analyze(RawMessage(uid="t", raw=eml(date_header), origin="t"))
    assert record is not None
    return record.date


class TokyoTZ(unittest.TestCase):
    """手元のタイムゾーンを JST に固定して見る。

    astimezone() は実行環境のタイムゾーンを使うので、固定しないと
    テストの結果が実行した機械に依存する。TZ 環境変数と tzset() で
    プロセスに閉じた形で差し替え、後片付けまでする。
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not hasattr(time, "tzset"):
            raise unittest.SkipTest("tzset が無い環境ではタイムゾーンを固定できない")
        cls._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Tokyo"
        time.tzset()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = cls._tz
        time.tzset()


class DateHeaderToLocalDate(TokyoTZ):
    """1通の Date ヘッダが、どの日付になるか。"""

    def test_negative_offset_crosses_into_next_day(self):
        # 実際に落ちた1通。-0700 の 7/15 10:43 は JST では 7/16 02:43。
        self.assertEqual(date_of("Wed, 15 Jul 2026 10:43:00 -0700"), "2026-07-16")

    def test_negative_offset_late_evening_crosses_into_next_day(self):
        # 明細CSVで 07-16 と出ていた側。JST では 7/17 14:00。
        self.assertEqual(date_of("Thu, 16 Jul 2026 22:00:00 -0700"), "2026-07-17")

    def test_positive_offset_before_local_midnight_stays(self):
        # +0900 の 08:30 は UTC では前日。UTC に寄せると前日に落ちる形。
        self.assertEqual(date_of("Thu, 15 Jan 2026 08:30:00 +0900"), "2026-01-15")

    def test_far_positive_offset_crosses_into_previous_day(self):
        # 逆向き(手元より進んだタイムゾーン)も見る。
        # +1300 は JST より4時間先なので、境界はその日の 04:00 に来る。
        self.assertEqual(date_of("Thu, 16 Jul 2026 04:00:00 +1300"), "2026-07-16")
        self.assertEqual(date_of("Thu, 16 Jul 2026 03:00:00 +1300"), "2026-07-15")

    def test_utc_early_morning_becomes_next_day(self):
        self.assertEqual(date_of("Wed, 15 Jul 2026 16:00:00 +0000"), "2026-07-16")

    def test_no_offset_is_read_as_local_wall_clock(self):
        # タイムゾーンの記載が無い Date は、書かれた時刻をそのまま日付にする。
        # 分からないものを勝手に UTC とみなすと、ここでも1日ずれる。
        self.assertEqual(date_of("Wed, 15 Jul 2026 02:43:00"), "2026-07-15")

    def test_minus_zero_zero_zero_zero_is_read_as_local_wall_clock(self):
        # -0000 は「オフセット不明」の意味(RFC 5322)。記載なしと同じ扱いにする。
        self.assertEqual(date_of("Wed, 15 Jul 2026 02:43:00 -0000"), "2026-07-15")

    def test_month_follows_the_local_date(self):
        # 集計の単位は月。日付が月をまたぐなら month も一緒に動かないと、
        # 月別の合計だけが古い解釈のまま残る。
        record = analyze(RawMessage(
            uid="t", raw=eml("Tue, 30 Jun 2026 22:00:00 -0700"), origin="t"))
        self.assertEqual((record.date, record.month), ("2026-07-01", "2026-07"))

    def test_unreadable_date_is_not_dropped(self):
        record = analyze(RawMessage(uid="t", raw=eml("これは日付ではない"), origin="t"))
        self.assertEqual(record.date, "不明")

    def test_out_of_range_date_does_not_raise(self):
        # 西暦9999年の大晦日を JST へ直すと、暦の表現範囲を外れる。
        # 例外で走査ごと止まるより、日付不明として通す。
        self.assertIsNone(local_datetime("Fri, 31 Dec 9999 23:59:59 -1200"))

    def test_non_ascii_date_header_does_not_stop_the_run(self):
        # Date に非 ASCII が入ると get() は str ではなく Header を返す。
        # そのまま渡すと AttributeError で、その1通ではなく走査全体が止まる。
        self.assertIsNone(local_datetime(
            email.message_from_bytes("Date: 令和8年7月16日\r\n\r\n".encode()).get("Date")))


def run_cli(mails: dict[str, bytes], *args: str) -> tuple[int, list[dict]]:
    """.eml を書いたフォルダに CLI をかけ、明細CSVの行を返す。

    collect() の絞り込みだけでなく、CSV に出るところまで通す。
    1件も残らなかった場合、明細CSVは書かれないので空の一覧になる。
    """
    with TemporaryDirectory() as name:
        root = Path(name)
        for filename, raw in mails.items():
            (root / filename).write_bytes(raw)
        detail = root / "detail.csv"
        with redirect_stderr(io.StringIO()):
            code = main([
                "--source", "eml-dir", "--input-dir", str(root),
                "--output", str(root / "summary.csv"),
                "--detail-output", str(detail), "--quiet", *args,
            ])
        if not detail.exists():
            return code, []
        with open(detail, encoding="utf-8-sig", newline="") as f:
            return code, list(csv.DictReader(f))


class PeriodFilterBoundary(TokyoTZ):
    """--since / --until が、手元の暦で効いているか。"""

    # 報告された6通。-0700 の 7/15 10:43〜10:59 = JST 7/16 02:43〜02:59。
    PAYPAL_SIX = {
        f"paypal-{i}.eml": eml(f"Wed, 15 Jul 2026 10:{minute}:00 -0700", ident=str(i))
        for i, minute in enumerate(("43", "46", "49", "52", "55", "59"))
    }

    def test_since_keeps_mails_that_are_on_the_boundary_locally(self):
        code, rows = run_cli(self.PAYPAL_SIX, "--since", "2026-07-16")
        self.assertEqual(code, 0)
        self.assertEqual(len(rows), 6)
        self.assertEqual({r["日付"] for r in rows}, {"2026-07-16"})
        self.assertEqual(sum(int(r["金額"]) for r in rows), 3780)

    def test_since_still_excludes_the_day_before(self):
        # 広げすぎていないことも見る。JST 7/15 23:59 は 7/16 に入らない。
        mails = {"before.eml": eml("Wed, 15 Jul 2026 07:59:00 -0700")}
        code, rows = run_cli(mails, "--since", "2026-07-16")
        self.assertEqual(code, 0)
        self.assertEqual(rows, [])

    def test_until_excludes_mails_that_fall_on_the_next_day_locally(self):
        # -0700 の 7/16 22:00 は JST の 7/17。7/16 までの指定には入らない。
        mails = {"after.eml": eml("Thu, 16 Jul 2026 22:00:00 -0700")}
        code, rows = run_cli(mails, "--until", "2026-07-16")
        self.assertEqual(code, 0)
        self.assertEqual(rows, [])

    def test_until_keeps_mails_on_the_last_day(self):
        mails = {"edge.eml": eml("Thu, 16 Jul 2026 06:00:00 -0700")}   # JST 7/16 22:00
        code, rows = run_cli(mails, "--until", "2026-07-16")
        self.assertEqual(code, 0)
        self.assertEqual([r["日付"] for r in rows], ["2026-07-16"])

    def test_detail_csv_shows_the_local_date(self):
        # 明細CSVの「日付」列。JST で受け取った日と一致していないと、
        # 手元のメール一覧と突き合わせられない。
        mails = {"a.eml": eml("Thu, 16 Jul 2026 22:00:00 -0700")}
        code, rows = run_cli(mails)
        self.assertEqual(code, 0)
        self.assertEqual([r["日付"] for r in rows], ["2026-07-17"])

    def test_mails_without_a_readable_date_survive_the_filter(self):
        mails = {"nodate.eml": eml("これは日付ではない")}
        code, rows = run_cli(mails, "--since", "2026-07-16", "--until", "2026-07-16")
        self.assertEqual(code, 0)
        self.assertEqual([r["日付"] for r in rows], ["不明"])


class ImapSinceWindow(unittest.TestCase):
    """サーバ側の SINCE は、手元で直す前に効いてしまう。"""

    def test_imap_date_formats_without_locale(self):
        self.assertEqual(imap_date("2026-07-16"), "16-Jul-2026")

    def test_search_window_starts_a_day_early(self):
        # SINCE は内部日付を「時刻とタイムゾーンを無視して」比べる(RFC 3501)。
        # 指定日ちょうどで投げると、手元の暦では 7/16 のメールが
        # サーバ側の 7/15 として弾かれ、取り返せない。
        self.assertEqual(imap_date("2026-07-16", shift_days=-1), "15-Jul-2026")

    def test_search_window_crosses_month_and_year(self):
        self.assertEqual(imap_date("2026-01-01", shift_days=-1), "31-Dec-2025")
        self.assertEqual(imap_date("2026-03-01", shift_days=-1), "28-Feb-2026")

    def test_bad_format_still_raises(self):
        # 黙って ALL に落とさない、という既存の約束を壊していないこと。
        with self.assertRaises(ValueError):
            imap_date("2026/07/16", shift_days=-1)


if __name__ == "__main__":
    unittest.main()
