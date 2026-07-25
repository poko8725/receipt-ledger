"""フォルダに書き出し済みの .eml を読むソース。

Mail.app の「書き出す」やブラウザ版と同じ入力。
フルディスクアクセスが不要なので、権限を与えたくない場合の逃げ道でもある。
"""

from __future__ import annotations

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
        for path in sorted(self.input_dir.rglob("*.eml")):
            try:
                raw = path.read_bytes()
            except (PermissionError, OSError):
                continue
            yield RawMessage(uid=str(path), raw=raw, origin=path.name)
