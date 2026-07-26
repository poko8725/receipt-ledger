"""フォルダに書き出し済みの .eml を読むソース。

Mail.app の「書き出す」やブラウザ版と同じ入力。
フルディスクアクセスが不要なので、権限を与えたくない場合の逃げ道でもある。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

from .base import RawMessage, SourceUnavailable


class EmlDirSource:
    name = "eml-dir"

    def __init__(self, input_dir: Path):
        self.input_dir = Path(input_dir)

    def check(self) -> None:
        if not self.input_dir.exists():
            raise SourceUnavailable(
                f"{self.input_dir} が存在しません。",
                "--input-dir に .eml を書き出したフォルダを指定してください。",
            )
        if not self.input_dir.is_dir():
            raise SourceUnavailable(
                f"{self.input_dir} はフォルダではありません。",
                "フォルダを指定してください(単体ファイルではなく)。",
            )

    def iter_messages(self) -> Iterator[RawMessage]:
        # Outlook から書き出すと .msg になることがある。読めないので拾わないが、
        # 黙って無視すると「件数が足りない」理由が利用者に伝わらない。
        others = sorted(self.input_dir.rglob("*.msg"))
        if others:
            print(
                f"注意: .msg が {len(others)} 件あります。Outlook 独自形式なので読めません。\n"
                "      .eml で書き出し直してください。",
                file=sys.stderr,
            )

        for path in sorted(self.input_dir.rglob("*.eml")):
            try:
                raw = path.read_bytes()
            except (PermissionError, OSError):
                continue
            yield RawMessage(uid=str(path), raw=raw, origin=path.name)
