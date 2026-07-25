"""Mail.app のローカル保存領域を直接読むソース。

書き出し作業が不要になる代わりに、フルディスクアクセスが要る。

保存場所:
    ~/Library/Mail/V<n>/<アカウントUUID>/<メールボックス>.mbox/.../Messages/*.emlx

V の番号は macOS のバージョンごとに変わる(V2〜V11 など)ので決め打ちせず V* で拾う。

.emlx の構造:
    1行目   : 後続するメッセージ本体のバイト数(10進)
    2行目以降: RFC 822 のバイト列(上の長さぶん)
    その後   : Mail.app 独自のメタデータ plist

添付を別管理している .partial.emlx も同じ構造なので同様に扱える。
本文だけ入っていれば金額は取れるので、添付が欠けていても支障はない。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from .base import MailSource, RawMessage, SourceUnavailable

FDA_HINT = (
    "Mail.app のデータはフルディスクアクセスで保護されています。\n"
    "  システム設定 → プライバシーとセキュリティ → フルディスクアクセス\n"
    "  を開き、このコマンドを実行しているアプリ(Terminal.app / iTerm など)を追加して\n"
    "  有効にしてください。追加後はそのアプリを再起動する必要があります。\n"
    "\n"
    "  設定を直接開く:\n"
    '    open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"'
)


def read_emlx(data: bytes) -> bytes | None:
    """.emlx のラッパを剥がして RFC 822 部分だけ返す。

    壊れている場合は None。長さ行が信用できないケースもあるので、
    宣言された長さが実データを超えるときは残り全部を返して解析に回す。
    """
    newline = data.find(b"\n")
    if newline == -1:
        return None
    try:
        length = int(data[:newline].strip())
    except ValueError:
        # 長さ行がない = 素の .eml が .emlx を名乗っている可能性。そのまま渡す。
        return data
    if length <= 0:
        return None
    body = data[newline + 1 :]
    return body[:length] if length <= len(body) else body


class AppleMailSource:
    """Mail.app のローカルメッセージを走査する。"""

    name = "apple-mail"

    def __init__(self, mail_dir: Path | None = None):
        self.mail_dir = mail_dir or (Path.home() / "Library" / "Mail")

    # -- 事前チェック -----------------------------------------------------

    def _roots(self) -> list[Path]:
        """V* ディレクトリ群。権限がなければ SourceUnavailable。"""
        try:
            entries = list(os.scandir(self.mail_dir))
        except PermissionError:
            raise SourceUnavailable(
                f"{self.mail_dir} を読む権限がありません。", FDA_HINT
            ) from None
        except FileNotFoundError:
            raise SourceUnavailable(
                f"{self.mail_dir} が見つかりません。"
                " Mail.app でアカウントを設定していない可能性があります。",
                "Mail.app を一度起動してアカウントを追加してから再実行してください。",
            ) from None
        return [Path(e.path) for e in entries if e.is_dir() and e.name.startswith("V")]

    def check(self) -> None:
        roots = self._roots()
        if not roots:
            raise SourceUnavailable(
                f"{self.mail_dir} にメールデータ(V* ディレクトリ)が見つかりません。",
                "Mail.app でアカウントを設定しているか確認してください。"
                " 権限不足でも空に見えることがあるため、その場合は\n" + FDA_HINT,
            )

    # -- 走査 -------------------------------------------------------------

    def iter_messages(self) -> Iterator[RawMessage]:
        for root in self._roots():
            # rglob は途中で PermissionError を投げうるので os.walk で握りつぶす
            for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda e: None):
                for fname in filenames:
                    if not fname.endswith(".emlx"):
                        continue
                    path = Path(dirpath) / fname
                    try:
                        raw = read_emlx(path.read_bytes())
                    except (PermissionError, OSError):
                        continue
                    if not raw:
                        continue
                    yield RawMessage(
                        uid=str(path),
                        raw=raw,
                        origin=fname,
                        meta={"mailbox": _mailbox_of(path)},
                    )


def _mailbox_of(path: Path) -> str:
    """パスから .mbox 名を拾う(どのメールボックス由来か分かるように)。"""
    for part in path.parts:
        if part.endswith(".mbox"):
            return part[: -len(".mbox")]
    return ""
