"""メール取得元のレジストリ。

新しい取得元(Gmail / IMAP / Outlook など)を足す手順は3つだけ:

  1. sources/ に1ファイル追加し、base.MailSource を満たすクラスを書く
     必要なのは name / check() / iter_messages() の3つ。
  2. 下の SOURCES に1行登録する。
  3. __main__.py の build_source() に引数の受け渡しを足す。

解析(analyze.py)・集計/出力(report.py)は一切触らない。
どのソースも RawMessage(RFC 822 のバイト列)を吐くところまでで責務が終わるため。

Gmail を足す場合の見取り図:
    users.messages.list  で対象を絞り込み(例: q="category:purchases")
    users.messages.get(format="raw") が base64url の RFC 822 を返す
    → base64.urlsafe_b64decode して RawMessage.raw に入れる
  認証情報の置き場と OAuth の同意フローだけが追加で必要になる。
"""

from __future__ import annotations

from .apple_mail import AppleMailSource
from .base import MailSource, RawMessage, SourceUnavailable
from .eml_dir import EmlDirSource

SOURCES: dict[str, str] = {
    "apple-mail": "Mail.app のローカルデータを直接読む(要フルディスクアクセス)",
    "eml-dir": "書き出し済みの .eml が入ったフォルダを読む",
}

__all__ = [
    "AppleMailSource",
    "EmlDirSource",
    "MailSource",
    "RawMessage",
    "SourceUnavailable",
    "SOURCES",
]
