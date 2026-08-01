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
from receipt_ledger.console import enable_utf8_output  # noqa: E402
from receipt_ledger.report import csv_safe  # noqa: E402
from receipt_ledger.rules import collapse_duplicates  # noqa: E402
from receipt_ledger.sources.base import RawMessage  # noqa: E402


def _js_number(value: float) -> str:
    """JS の String(number) に合わせる。1220.0 ではなく "1220"。"""
    return str(int(value)) if float(value).is_integer() else repr(value)

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
        row = {
            "subject": record.subject,
            "sender": record.sender,
            "merchant": record.merchant,
            "item": record.item,
            "amount": float(record.amount),
            "currency": record.currency,
            "date": record.date,
        }
        # CSV に書く直前の形。解析結果が同じでも、ここで割れれば
        # 片方の出力だけ数式として実行される。
        # JS 側は数値を String() で文字列にしてから通すので、揃える。
        row["csv_cells"] = [
            csv_safe(v) for v in [
                row["date"], row["merchant"], row["item"], row["currency"],
                _js_number(row["amount"]), row["sender"], row["subject"],
            ]
        ]
        row["_record"] = record
        results[path.name] = row

    # 寄せ処理は1通ずつでは判定できない。全件を渡した結果を、
    # 「どれに寄せられたか」として1件ずつの行に書き戻す。
    # こうすると既存の突き合わせ機構がそのまま使えて、装置の守備範囲が広がる。
    rows = {name: row for name, row in results.items() if row}
    by_record = {id(row["_record"]): name for name, row in rows.items()}
    _, dropped = collapse_duplicates([row["_record"] for row in rows.values()])
    for row in rows.values():
        row["duplicate_of"] = ""
    for record, kept in dropped:
        rows[by_record[id(record)]]["duplicate_of"] = by_record[id(kept)]
    for row in rows.values():
        del row["_record"]
    return results


if __name__ == "__main__":
    enable_utf8_output()
    json.dump(run(), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
