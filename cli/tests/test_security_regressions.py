"""2026-07-26 のセキュリティレビューで直した箇所が、戻っていないこと。

    cd cli && python3 -m unittest discover tests

直したときテストを書かなかったので、どれも「引数を1つ消す」「上限を外す」
だけで静かに元に戻る。しかも戻っても例外は出ず、動きも変わらない。
気づける形にしておくのがここ。

数式インジェクション(3件目)は test_csv_output.py にある。
"""

from __future__ import annotations

import imaplib
import re
import ssl
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from receipt_ledger.rules import _NUM, extract_amount  # noqa: E402
from receipt_ledger.sources.imap import ImapSource  # noqa: E402


class RegexBacktracking(unittest.TestCase):
    """長い数字列で正規表現が破綻しないこと(ReDoS)。

    金額の正規表現は3本あり、3本目は接尾の通貨(円/USD/...)を必須にしている。
    通貨の手がかりが無い数字列を渡すと、この3本目が**どの開始位置でも失敗する**。
    上限の無い `[\\d,]*` だと、位置ごとに「全部消費して失敗、1文字戻して
    また失敗」を繰り返すので、全体が桁数の2乗で効いてくる。

    メールの本文は第三者が決められるので、これは一括処理を止める手段になる。
    """

    # 上限を外した実装での実測(この機械): 2千桁 0.10s / 1万桁 2.5s / 2万桁 10.0s。
    # 現行は 2万桁 0.025s、20万桁 0.23s で、桁数に対して線形。
    # 400倍の開きがあるので、遅い機械で測っても判定はぶれない。
    CEILING_SECONDS = 2.0

    def test_long_digit_run_without_currency_finishes_quickly(self):
        body = "1" * 20_000
        started = time.perf_counter()
        result = extract_amount(body)
        elapsed = time.perf_counter() - started

        # 「速いが間違っている」で通らないよう、結果も見る。
        # 通貨の手がかりが無いので、金額として拾ってはいけない。
        self.assertIsNone(result)
        self.assertLess(
            elapsed, self.CEILING_SECONDS,
            f"2万桁の数字列に {elapsed:.2f} 秒かかった。"
            "_NUM の繰り返しから上限が外れていないか",
        )

    def test_the_repetition_has_an_upper_bound(self):
        """時間を測らずに、上限そのものを見る。

        時間の比較は機械の速さに左右されるので、実装の性質も直接見ておく。
        `\\d[\\d,]{0,19}` なので、1つのマッチは最長21文字にしかならない。
        """
        match = re.search(_NUM, "1234567890123456789012345")
        self.assertIsNotNone(match)
        self.assertLessEqual(
            len(match.group(0)), 21,
            "繰り返しの上限が外れている(上限なしだと数字列を丸ごと飲む)",
        )

    def test_ordinary_amounts_still_parse(self):
        """上限を置いたことで、普通の金額が拾えなくなっていないこと。"""
        for body, expected in [
            ("合計 ¥1,780", "1780"),
            ("Total $43.00", "43.00"),
            ("お支払金額 3,980円", "3980"),
        ]:
            with self.subTest(body=body):
                got = extract_amount(body)
                self.assertIsNotNone(got, f"{body} から金額を取れなくなっている")


class TlsVerification(unittest.TestCase):
    """IMAP の接続で証明書を検証していること。

    imaplib.IMAP4_SSL は ssl_context を渡さないと ssl._create_stdlib_context()
    を使い、check_hostname=False / verify_mode=CERT_NONE になる。つまり
    **引数を1つ消すだけで、警告も例外も無しに検証が止まる**。
    中間者にアプリパスワードを渡しうるので、明示していることを見る。

    ネットワークには出ない。IMAP4_SSL を差し替えて、渡された文脈だけを見る。
    """

    def connect_and_capture(self) -> ssl.SSLContext | None:
        captured: dict = {}

        class FakeConnection:
            def login(self, user, password):
                captured["login"] = (user, password)

        def fake_imap4_ssl(host, *args, ssl_context=None, **kwargs):
            captured["host"] = host
            captured["ssl_context"] = ssl_context
            return FakeConnection()

        source = ImapSource(host="imap.example.invalid")
        source.user, source.password = "user@example.invalid", "app-password"

        with mock.patch.object(imaplib, "IMAP4_SSL", fake_imap4_ssl):
            source.connect()

        self.assertEqual(captured.get("host"), "imap.example.invalid")
        self.assertIn("login", captured, "login まで到達していない")
        return captured.get("ssl_context")

    def test_an_ssl_context_is_passed(self):
        context = self.connect_and_capture()
        self.assertIsNotNone(
            context,
            "ssl_context を渡していない。imaplib の既定は CERT_NONE なので、"
            "この引数が消えると黙って検証されなくなる",
        )

    def test_the_context_verifies_the_certificate(self):
        context = self.connect_and_capture()
        # 文脈が無いまま属性を見ると AttributeError になり、
        # 「落ちた理由」が読み取れない。失敗として出す。
        self.assertIsNotNone(context, "ssl_context が渡っていない")
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_the_context_checks_the_hostname(self):
        """証明書が有効でも、宛先と一致していなければ意味がない。"""
        context = self.connect_and_capture()
        self.assertIsNotNone(context, "ssl_context が渡っていない")
        self.assertTrue(context.check_hostname)


if __name__ == "__main__":
    unittest.main()
