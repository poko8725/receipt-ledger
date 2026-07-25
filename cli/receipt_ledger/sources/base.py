"""メール取得元(ソース)の共通インタフェース。

受取帳の設計上の中心。ソースがやることは1つだけ:

    「RFC 822 のバイト列を1通ずつ吐き出す」

解析(マーチャント判定・金額抽出)はソースを問わず analyze.py が受け持つので、
新しい取得元を足すときに解析側を触る必要はない。

この形にしてあるのは、Mail.app / Gmail / IMAP がどれも最終的には
RFC 822 のバイト列を取り出せるから:

    Mail.app  .emlx ファイルから長さヘッダを剥がす
    Gmail     users.messages.get(format="raw") の base64url をデコード
    IMAP      FETCH (RFC822)

つまりどれも RawMessage に落ちる。ここが揃っている限り、
取得元が増えても下流は無変更で済む。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol, runtime_checkable


@dataclass
class RawMessage:
    """解析前の1通。中身には触らない。"""

    uid: str
    """ソース内で一意な識別子。複数ソースをまたぐ重複排除に使う。
    ファイルなら絶対パス、Gmail なら message id。"""

    raw: bytes
    """RFC 822 のバイト列そのもの。デコードはしない(charset の判定は解析側の仕事)。"""

    origin: str = ""
    """エラー表示用の人間可読な出所。ファイル名など。"""

    meta: dict = field(default_factory=dict)
    """ソース固有の付加情報。解析には使わない。"""


class SourceUnavailable(Exception):
    """ソースが使える状態にない(権限がない・未設定など)。

    hint には利用者が次に何をすればよいかを必ず入れる。
    """

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


@runtime_checkable
class MailSource(Protocol):
    """メール取得元。

    新しいソースを追加する手順:
      1. このプロトコルを満たすクラスを sources/ に1ファイルで書く
      2. sources/__init__.py の SOURCES に登録する
    解析側・出力側の変更は不要。
    """

    name: str

    def check(self) -> None:
        """使える状態か確認する。駄目なら SourceUnavailable を投げる。

        iter_messages を呼ぶ前に実行される。権限や設定の不備を
        「0件でした」ではなく明確なエラーとして出すための入口。
        """
        ...

    def iter_messages(self) -> Iterator[RawMessage]:
        """1通ずつ yield する。件数が多くなりうるのでリストにしない。"""
        ...
