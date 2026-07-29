"""IMAP からメールを直接読むソース。

書き出し作業が要らない。Mail.app 直読みと違って OS を問わず、
Outlook の書き出し（新しい版は一括不可、従来版は .msg）も迂回できる。
**サーバ側にある RFC 822 の原本をそのまま受け取る**ので、形式の劣化もない。

## 資格情報は保存しない

環境変数からしか読まない。設定ファイルにも OS の資格情報ストアにも書かない。

    IMAP_USER  メールアドレス
    IMAP_PASS  アプリパスワード（2段階認証を有効にして発行する）
    IMAP_HOST  既定は imap.gmail.com

預かった時点で、漏らさない責任が発生する。**持たなければその責任も無い。**
保存したい人は OS の仕組みで各自やればよく、この道具が肩代わりする必要はない。

## 「外部に送らない」との関係

取りに行く先は、元々自分のメールが置いてあるサーバである。
第三者へは何も送っていないので、訴求は崩れない。
（ブラウザ版は今も外部通信を CSP で禁じたままで、こちらは CLI 版だけの機能）

## 対応しないと決めたこと

**Exchange Online で IMAP が閉じている環境**には対応しない。
開いていても Microsoft は基本認証を廃止しているので OAuth が要り、
アプリ登録と管理者同意まで踏むことになる。
個人が自分の経費を処理するために背負う手順ではないと判断した。

その環境では `.eml` をフォルダへ書き出して `--source eml-dir` を使う。
逃げ道は常に残してある。
"""

from __future__ import annotations

import imaplib
import os
import ssl
from datetime import datetime, timedelta
from typing import Iterator

from .base import MailSource, RawMessage, SourceUnavailable

DEFAULT_HOST = "imap.gmail.com"

# IMAP は英語3文字の月名を要求する。strftime("%b") はロケールに従うので、
# locale.setlocale(LC_TIME, "") を誰かが呼んだ瞬間に "01-1-2026" になって壊れる。
# 呼ばれない前提に寄りかからず、自前の表を使う。
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def imap_date(value: str, shift_days: int = 0) -> str:
    """YYYY-MM-DD を IMAP の SEARCH が受け取る形式にする。

    書式が違えば例外にする。**黙って ALL に落とさない。**
    落とすと「サーバから全件取ってきて、手元で全件捨てる」という
    一番遅くて一番間違った動きになり、0 件が返る理由も分からなくなる。
    """
    day = datetime.strptime(value, "%Y-%m-%d")   # 不正なら ValueError
    day += timedelta(days=shift_days)
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def encode_folder(name: str) -> str:
    """フォルダ名を修正 UTF-7 (RFC 3501) にする。

    IMAP のコマンドは ASCII 前提なので、日本語のフォルダ名はそのまま渡せない。
    ここでつまずくと「フォルダが無い」と誤診するので、変換を用意しておく。
    ただし**一番確実なのは --list-folders の出力をそのまま使うこと**。
    """
    out = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        encoded = "".join(buffer).encode("utf-16-be")
        import base64
        b64 = base64.b64encode(encoded).decode("ascii").rstrip("=")
        out.append("&" + b64.replace("/", ",") + "-")
        buffer.clear()

    for ch in name:
        if ch == "&":
            flush()
            out.append("&-")
        elif 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append(ch)
        else:
            buffer.append(ch)
    flush()
    return "".join(out)


class ImapSource:
    name = "imap"

    def __init__(self, folder: str = "INBOX", since: str | None = None,
                 host: str | None = None):
        self.folder = folder
        self.since = since
        self.host = host or os.environ.get("IMAP_HOST") or DEFAULT_HOST
        self.user = os.environ.get("IMAP_USER")
        self.password = os.environ.get("IMAP_PASS")
        self._conn: imaplib.IMAP4_SSL | None = None

    def check(self) -> None:
        missing = [k for k, v in (("IMAP_USER", self.user), ("IMAP_PASS", self.password)) if not v]
        if missing:
            raise SourceUnavailable(
                f"環境変数 {' と '.join(missing)} が設定されていません。",
                "この道具は資格情報を保存しません。実行するシェルで設定してください。\n"
                "  アプリパスワードは、2段階認証を有効にしたうえで発行します。\n"
                "\n"
                "  macOS / Linux:\n"
                "    export IMAP_USER='自分のアドレス'\n"
                "    export IMAP_PASS='アプリパスワード'\n"
                "\n"
                "  Windows (PowerShell):\n"
                "    $env:IMAP_USER = '自分のアドレス'\n"
                "    $env:IMAP_PASS = 'アプリパスワード'",
            )

    def connect(self) -> imaplib.IMAP4_SSL:
        if self._conn is not None:
            return self._conn
        try:
            # imaplib は ssl_context を渡さないと ssl._create_stdlib_context() を使い、
            # **check_hostname=False / verify_mode=CERT_NONE** になる。
            # 証明書を検証しないので、中間者にアプリパスワードを渡しうる。
            # 既定に任せず、検証する文脈を明示する。
            conn = imaplib.IMAP4_SSL(self.host, ssl_context=ssl.create_default_context())
            conn.login(self.user, self.password)
        except imaplib.IMAP4.error as e:
            # 例外に資格情報が混ざらないよう、メッセージは自分で組み立てる
            raise SourceUnavailable(
                f"{self.host} にログインできませんでした。",
                "アプリパスワードが正しいか、IMAP が有効かを確認してください。\n"
                f"  サーバからの応答: {type(e).__name__}",
            ) from None
        except OSError as e:
            raise SourceUnavailable(
                f"{self.host} に接続できませんでした。",
                f"ネットワークとホスト名を確認してください（{e.__class__.__name__}）。",
            ) from None
        self._conn = conn
        return conn

    def list_folders(self) -> list[str]:
        """フォルダ一覧。日本語名は修正 UTF-7 のまま返す。

        推測させないために用意している。ここに出た文字列をそのまま
        --imap-folder に渡すのが確実。
        """
        conn = self.connect()
        ok, rows = conn.list()
        if ok != "OK":
            return []
        names = []
        for row in rows or []:
            text = row.decode("ascii", "replace")
            # (\HasNoChildren) "/" "名前" の形
            if '"' in text:
                names.append(text.rsplit('"', 2)[-2])
        return names

    def iter_messages(self) -> Iterator[RawMessage]:
        conn = self.connect()
        folder = self.folder if self.folder.isascii() else encode_folder(self.folder)
        ok, _ = conn.select(f'"{folder}"', readonly=True)
        if ok != "OK":
            raise SourceUnavailable(
                f"フォルダ {self.folder} を開けませんでした。",
                "--list-folders で一覧を出し、その文字列をそのまま指定してください。\n"
                "  日本語名は修正 UTF-7 で表されるので、見た目と一致しません。",
            )

        # 日付の絞り込みはサーバ側でやる。全件取ってから捨てるのは無駄。
        # ただし1日ぶん手前から取る。SINCE はサーバが持つ内部日付を
        # 「時刻とタイムゾーンを無視して」比べる規定(RFC 3501)なので、
        # 手元の暦では指定日に入るメールが、サーバ側では前日として弾かれうる。
        # ここで弾かれると、手元でタイムゾーンを直しても取り返せない。
        # タイムゾーンの差は最大でも26時間＝日付で1日なので、1日で足りる。
        # 余分に取った分は collect() が record.date で正しく落とす。
        criteria = "ALL"
        if self.since:
            criteria = f'(SINCE "{imap_date(self.since, shift_days=-1)}")'

        ok, data = conn.search(None, criteria)
        if ok != "OK":
            return
        for num in (data[0].split() if data and data[0] else []):
            ok, payload = conn.fetch(num, "(RFC822)")
            if ok != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            raw = payload[0][1]
            yield RawMessage(
                uid=f"imap:{self.host}:{self.folder}:{num.decode()}",
                raw=raw,
                origin=f"{self.folder}",
                meta={"mailbox": self.folder},
            )

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None
