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
import unicodedata
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


# 既定で走査しないメールボックス。理由は2つある。
#
# **捨てたもの**(ゴミ箱・迷惑メール)
#   捨てたはずの広告メールが集計に戻ってくる。実測では7月後半の余剰10件のうち
#   7件がゴミ箱由来で、内訳は広告・当選通知・料金改定のお知らせだった。
#   利用者が捨てた時点で「取引ではない」と判断済みのものを、道具が拾い直している。
#
# **受け取ったものではない**(下書き・送信済み・Outbox)
#   自分が書いたメールは領収書ではない。しかも下書きは Mail.app が自動保存の版を
#   別ファイルで持ち、まだ Message-ID が付いていないので重複判定も素通りする。
#   実測では、書きかけの1通が5桁の取引4件として計上されていた。
#
# Mail.app のメールボックス名はアカウントの言語と種別で変わる(Gmail は
# `[Gmail].mbox/ゴミ箱.mbox` と入れ子、IMAP は `Deleted Messages.mbox`)ので、
# 名前を並べて突き合わせる。入れ子のどの段に当たっても除外する。
EXCLUDED_MAILBOXES = {
    # 捨てたもの
    "ゴミ箱", "迷惑メール", "trash", "junk", "spam", "bulk mail",
    "deleted messages", "deleted items", "junk e-mail",
    # 受け取ったものではない
    "下書き", "送信済みメール", "送信済み", "drafts", "draft",
    "sent messages", "sent", "sent items", "outbox", "sendlater",
}


def mailbox_key(name: str) -> str:
    """メールボックス名を突き合わせ用の形にする。`.mbox` は付いていてもよい。

    **NFC に正規化するのが要点。**macOS のファイル名は濁点が分解された形(NFD)で
    保存される。`"ゴミ箱"` と書いたリテラルは NFC なので、素朴に比較すると
    一致せず、**除外しているつもりのゴミ箱を読み続ける**。
    エラーは出ず、件数が増えるだけなので気づけない。
    濁点の無い `"迷惑メール"` だけ一致して、片方だけ効いていた。
    """
    if name.endswith(".mbox"):
        name = name[: -len(".mbox")]
    return unicodedata.normalize("NFC", name).lower()


def is_excluded(mailbox: str) -> bool:
    """既定で走査しないメールボックス名か。"""
    return mailbox_key(mailbox) in EXCLUDED_MAILBOXES


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

    def __init__(self, mail_dir: Path | None = None, include_excluded: bool = False,
                 mailboxes: list[str] | None = None):
        self.mail_dir = mail_dir or (Path.home() / "Library" / "Mail")
        self.include_excluded = include_excluded
        # 指定があればそこだけ見る。**指定は既定の除外より強い**
        # (ゴミ箱を名指ししたなら、ゴミ箱を読みたいということ)。
        self.mailboxes = list(mailboxes or [])
        self._wanted = {mailbox_key(m) for m in self.mailboxes}

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
        if self.mailboxes:
            self._check_mailboxes()

    def _check_mailboxes(self) -> None:
        """`--mailbox` の指定が実際に読めるかを、走査前に確かめる。

        **黙って 0 件になるのを防ぐのが目的。**名前を打ち間違えても、
        ローカルに実体が無くても、出るのは同じ「0 件」なので区別がつかない。

        Gmail のラベルは Mail.app 上では独立したメールボックスに見えるが、
        実体は `すべてのメール` にあり、ラベル側は `Info.plist` だけの空の器に
        なっていることがある(実測: `Apple からの領収書` が 0 通)。
        名前は合っているのに 0 件、という一番読みにくい形になる。
        """
        counts = self.mailbox_counts()
        known = {mailbox_key(name): name for name in counts}
        listing = "\n".join(f"    {name}" for name in sorted(counts))

        unknown = [m for m in self.mailboxes if mailbox_key(m) not in known]
        if unknown:
            raise SourceUnavailable(
                "指定されたメールボックスがありません: " + ", ".join(unknown),
                "使える名前:\n" + listing,
            )

        empty = [m for m in self.mailboxes if counts[known[mailbox_key(m)]] == 0]
        if len(empty) == len(self.mailboxes):
            raise SourceUnavailable(
                "指定されたメールボックスにローカルのメールがありません: "
                + ", ".join(empty),
                "Gmail のラベルは Mail.app 上では独立して見えますが、実体は\n"
                "  「すべてのメール」に入っていて、ラベル側は空の器になっていることがあります。\n"
                "  --mailbox すべてのメール を指定するか、--mailbox を外してください。\n\n"
                "  ローカルにメールがあるメールボックス:\n"
                + "\n".join(f"    {n} ({c} 通)" for n, c in sorted(counts.items()) if c),
            )

    def mailbox_counts(self) -> dict[str, int]:
        """メールボックス名 → ローカルにある .emlx の数。ファイルは読まない。"""
        counts: dict[str, int] = {}
        for root in self._roots():
            for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda e: None):
                name = _mailbox_of(Path(dirpath))
                if not name:
                    continue
                counts[name] = counts.get(name, 0) + sum(
                    1 for f in filenames if f.endswith(".emlx")
                )
        return counts

    # -- 走査 -------------------------------------------------------------

    def list_mailboxes(self) -> list[str]:
        """存在するメールボックス名。`--mailbox` に渡す値を確かめるため。

        入れ子もそれぞれ1件として並べる。名前は Mail.app の表示と同じで、
        ゴミ箱などの既定で除外するものも**印を付けて**出す。
        見えないものを指定しようがないため、一覧からは隠さない。
        """
        return sorted(self.mailbox_counts())

    def _selected(self, path: Path) -> bool:
        """`--mailbox` の指定に当たるか。入れ子のどの段に当たってもよい。"""
        if not self._wanted:
            return True
        return any(part.endswith(".mbox") and mailbox_key(part) in self._wanted
                   for part in path.parts)

    def iter_messages(self) -> Iterator[RawMessage]:
        for root in self._roots():
            # rglob は途中で PermissionError を投げうるので os.walk で握りつぶす
            for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
                if not self.include_excluded and not self._wanted:
                    # 降りる前に枝ごと落とす。走査対象から外すだけでなく速くなる。
                    dirnames[:] = [d for d in dirnames if not is_excluded(d)]
                if not self._selected(Path(dirpath)):
                    continue
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
    """パスから .mbox 名を拾う(どのメールボックス由来か分かるように)。

    **一番深い段を取る。**Gmail は `[Gmail].mbox/ゴミ箱.mbox/...` と入れ子になるので、
    最初に当たった段を返すと全部 `[Gmail]` になり、明細 CSV から
    どのメールボックス由来かが読めなくなる。
    """
    for part in reversed(path.parts):
        if part.endswith(".mbox"):
            return unicodedata.normalize("NFC", part[: -len(".mbox")])
    return ""
