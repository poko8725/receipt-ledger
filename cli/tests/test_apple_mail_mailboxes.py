"""どのメールボックスを読むか。

    cd cli && python3 -m unittest discover tests

ここで見ているのは1つだけ:

    **読まないと決めたメールボックスを、本当に読んでいないか。**

ゴミ箱を読むと、捨てたはずの広告メールが集計に戻ってくる。実データでは
7月後半の余剰10件のうち7件がゴミ箱由来だった。除外し損ねても例外は出ず、
件数と金額が増えるだけなので、テストが無ければ気づけない。

**フィクスチャのメールボックス名は NFD で作る。**macOS のファイル名は濁点が
分解された形で保存されるので、NFC のまま比較すると `"ゴミ箱"` が一致しない。
最初の実装がこれで、濁点の無い `"迷惑メール"` だけ効いていた。
NFC で書いたテストは、その状態でも通ってしまう。
"""

from __future__ import annotations

import sys
import unicodedata
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from receipt_ledger.sources.apple_mail import AppleMailSource, is_excluded  # noqa: E402
from receipt_ledger.sources.base import SourceUnavailable  # noqa: E402

EML = (
    "From: service@paypal.co.jp\r\n"
    "To: user@example.com\r\n"
    "Subject: PayPal\r\n"
    "Date: Wed, 15 Jul 2026 10:43:00 +0900\r\n"
    "Message-ID: <mbox-{n}@example.invalid>\r\n"
    "Content-Type: text/plain; charset=UTF-8\r\n"
    "\r\n"
    "合計 ¥630\r\n"
)


def build_mail_dir(root: Path, mailboxes: dict[str, int]) -> None:
    """Mail.app のディレクトリ構成を模して .emlx を置く。

    名前は NFD にする。本番のファイルシステムがそうなっているため。
    入れ子(`[Gmail].mbox/ゴミ箱.mbox`)も本番と同じ形で作る。
    """
    n = 0
    for name, count in mailboxes.items():
        parts = [unicodedata.normalize("NFD", p) + ".mbox" for p in name.split("/")]
        box = root / "V10" / "ACCOUNT-UUID"
        for part in parts:
            box = box / part
        messages = box / "UUID" / "Data" / "0" / "Messages"
        messages.mkdir(parents=True, exist_ok=True)
        for _ in range(count):
            n += 1
            body = EML.format(n=n).encode("utf-8")
            # .emlx は先頭にバイト数の行が付く
            (messages / f"{n}.emlx").write_bytes(
                str(len(body)).encode() + b"\n" + body
            )


class ExcludedByDefault(unittest.TestCase):
    def test_濁点付きの名前も除外できる(self):
        # NFD で来ても NFC で来ても同じ答えにならないと、片方だけ素通りする。
        self.assertTrue(is_excluded(unicodedata.normalize("NFD", "ゴミ箱")))
        self.assertTrue(is_excluded("ゴミ箱"))
        self.assertTrue(is_excluded("ゴミ箱.mbox"))
        self.assertTrue(is_excluded("迷惑メール"))
        self.assertTrue(is_excluded("Deleted Messages"))
        self.assertFalse(is_excluded("INBOX"))

    def test_自分が書いたメールボックスも除外する(self):
        # 下書きは自動保存の版が別ファイルで残り、Message-ID もまだ無いので
        # 重複判定を素通りする。実測では書きかけの1通が4件に化けていた。
        self.assertTrue(is_excluded("下書き"))
        self.assertTrue(is_excluded("送信済みメール"))
        self.assertTrue(is_excluded("Sent Messages"))
        self.assertTrue(is_excluded("Outbox"))

    def test_ゴミ箱と迷惑メールは既定で読まない(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_mail_dir(root, {"INBOX": 2, "[Gmail]/ゴミ箱": 3, "[Gmail]/迷惑メール": 4,
                                  "[Gmail]/下書き": 5, "[Gmail]/送信済みメール": 6})
            source = AppleMailSource(mail_dir=root)
            self.assertEqual(len(list(source.iter_messages())), 2)

    def test_all_mailboxes_なら読む(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_mail_dir(root, {"INBOX": 2, "[Gmail]/ゴミ箱": 3})
            source = AppleMailSource(mail_dir=root, include_excluded=True)
            self.assertEqual(len(list(source.iter_messages())), 5)


class MailboxSelection(unittest.TestCase):
    def test_指定したメールボックスだけ読む(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_mail_dir(root, {"INBOX": 2, "[Gmail]/すべてのメール": 5})
            source = AppleMailSource(mail_dir=root, mailboxes=["すべてのメール"])
            self.assertEqual(len(list(source.iter_messages())), 5)

    def test_名指しは既定の除外より強い(self):
        # ゴミ箱を名指ししたなら、ゴミ箱を読みたいということ。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_mail_dir(root, {"INBOX": 2, "[Gmail]/ゴミ箱": 3})
            source = AppleMailSource(mail_dir=root, mailboxes=["ゴミ箱"])
            self.assertEqual(len(list(source.iter_messages())), 3)

    def test_存在しない名前は走査前に止める(self):
        # 打ち間違えても「0 件」としか出ないと、対象が無いのか名前が違うのか
        # 区別がつかない。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_mail_dir(root, {"INBOX": 2})
            source = AppleMailSource(mail_dir=root, mailboxes=["領収書"])
            with self.assertRaises(SourceUnavailable) as caught:
                source.check()
            self.assertIn("領収書", str(caught.exception))

    def test_実体のない器を指定したら止める(self):
        # Gmail のラベルは Mail.app 上では独立して見えるのに、ローカルには
        # Info.plist しか無いことがある。名前は合っているのに 0 件になる。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_mail_dir(root, {"INBOX": 2, "Apple からの領収書": 0})
            source = AppleMailSource(mail_dir=root, mailboxes=["Apple からの領収書"])
            with self.assertRaises(SourceUnavailable) as caught:
                source.check()
            self.assertIn("ローカルのメールがありません", str(caught.exception))

    def test_通数つきで一覧できる(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_mail_dir(root, {"INBOX": 2, "[Gmail]/ゴミ箱": 3, "空のラベル": 0})
            counts = AppleMailSource(mail_dir=root).mailbox_counts()
            # 表示は NFC。一覧をコピーして --mailbox に貼れないと意味がない。
            self.assertEqual(counts["INBOX"], 2)
            self.assertEqual(counts["ゴミ箱"], 3)
            self.assertEqual(counts["空のラベル"], 0)


if __name__ == "__main__":
    unittest.main()
