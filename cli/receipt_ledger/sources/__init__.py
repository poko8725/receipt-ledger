"""メール取得元のレジストリ。

新しい取得元(Gmail / IMAP / Outlook など)を足す手順は3つだけ:

  1. sources/ に1ファイル追加し、base.MailSource を満たすクラスを書く
     必要なのは name / check() / iter_messages() の3つ。
  2. 下の SOURCES に1行登録する。
  3. __main__.py の build_source() に引数の受け渡しを足す。

解析(analyze.py)・集計/出力(report.py)は一切触らない。
どのソースも RawMessage(RFC 822 のバイト列)を吐くところまでで責務が終わるため。

IMAP は実装済み(imap.py)。実測で 1 件あたり 0.46 秒、解析は 20/20 通った。
Outlook の書き出し(新しい版は一括不可、従来版は .msg)を迂回できる。

対応しないと決めたもの:
  Outlook COM  取れるのが .msg で、OLE2 を解く依存が要る。形式が悪いほうに
               労力を払う理由がない。要望が出たら考える
  Exchange/Graph  管理者同意と OAuth が要る。個人向けの道具が背負う重さではない
  どちらも `.eml` をフォルダへ書き出す経路が逃げ道として常に残る。
"""

from __future__ import annotations

from .apple_mail import AppleMailSource
from .base import MailSource, RawMessage, SourceUnavailable
from .eml_dir import EmlDirSource
from .imap import ImapSource

SOURCES: dict[str, str] = {
    "apple-mail": "Mail.app のローカルデータを直接読む(要フルディスクアクセス)",
    "eml-dir": "書き出し済みの .eml が入ったフォルダを読む",
    "imap": "IMAP で直接読む(書き出し不要。IMAP_USER / IMAP_PASS が要る)",
}

__all__ = [
    "AppleMailSource",
    "EmlDirSource",
    "ImapSource",
    "MailSource",
    "RawMessage",
    "SourceUnavailable",
    "SOURCES",
]
