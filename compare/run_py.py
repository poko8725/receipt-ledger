"""Python 実装に fixtures を流し、比較用の JSON を吐く。

出力は run_js.py と同じ形にする。揃っていないと compare.py が意味を持たない。

    python3 compare/run_py.py > /tmp/py.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "cli"))

from receipt_ledger.analyze import analyze  # noqa: E402
from receipt_ledger.sources.base import RawMessage  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def require_fixtures() -> list[Path]:
    """フィクスチャは生成物なのでリポジトリに入っていない(.gitignore の *.eml)。
    無いまま走ると差分ゼロで通ってしまうので、ここで止める。"""
    paths = sorted(FIXTURES.glob("*.eml"))
    if not paths:
        sys.exit(
            "フィクスチャがありません。先に生成してください:\n"
            "    python3 compare/make_fixtures.py"
        )
    return paths


def run() -> dict[str, dict | None]:
    results: dict[str, dict | None] = {}
    for path in require_fixtures():
        record = analyze(RawMessage(uid=path.name, raw=path.read_bytes(), origin=path.name))
        if record is None:
            # 金額が取れないメールは「レシートではない」として捨てる。
            # 片方だけが捨てた場合こそ差分なので、null として残す。
            results[path.name] = None
            continue
        results[path.name] = {
            "subject": record.subject,
            "sender": record.sender,
            "merchant": record.merchant,
            "item": record.item,
            "amount": float(record.amount),
            "currency": record.currency,
            "date": record.date,
        }
    return results


if __name__ == "__main__":
    json.dump(run(), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
