"""この環境で受取帳 CLI が動くかを判定する。

    python3 cli/check_environment.py        （Windows なら py -3 cli\\check_environment.py）

前半は「いまの CLI が動くか」、後半は「経費用途の出力を足すときに
この OS で踏むことになる箇所」を実測する。判定は OK / NG で出る。

想像で「たぶん動く」と書かないために置いている。
"""

from __future__ import annotations

import csv
import io
import os
import platform
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "cli"))

from receipt_ledger.console import enable_utf8_output  # noqa: E402

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"[{'OK' if ok else 'NG'}] {name}" + (f" - {detail}" if detail else ""))


def main() -> None:
    enable_utf8_output()
    print(f"OS      : {platform.system()} {platform.release()}")
    print(f"Python  : {sys.version.split()[0]}")
    print(f"文字コード: stdout={sys.stdout.encoding} / fs={sys.getfilesystemencoding()}")
    print()

    # ---- いまの CLI が動くか ----
    check(sys.version_info >= (3, 10), "Python 3.10 以上",
          f"検出: {sys.version_info.major}.{sys.version_info.minor}")

    try:
        from receipt_ledger.analyze import analyze
        from receipt_ledger.sources.base import RawMessage
        check(True, "標準ライブラリのみで import できる")
    except Exception as e:
        check(False, "標準ライブラリのみで import できる", str(e))
        summary()
        return

    fixtures = ROOT / "compare" / "fixtures"
    if not list(fixtures.glob("*.eml")):
        print("\n  フィクスチャが無いので先に生成します: python3 compare/make_fixtures.py")
        sys.path.insert(0, str(ROOT / "compare"))
        try:
            import make_fixtures
            make_fixtures.main()
            print()
        except Exception as e:
            check(False, "フィクスチャの生成", str(e))

    parsed, failed = 0, []
    for path in sorted(fixtures.glob("*.eml")):
        try:
            record = analyze(RawMessage(uid=path.name, raw=path.read_bytes(), origin=path.name))
            if record is None:
                failed.append(f"{path.name}(金額取得できず)")
            else:
                parsed += 1
        except Exception as e:
            failed.append(f"{path.name}({e})")
    check(parsed > 0 and not failed, f".eml の解析（{parsed} 件成功）",
          "失敗: " + ", ".join(failed) if failed else "")

    # cp932 のコンソールでは ¥ も em dash も encode できず、表示の一箇所で全体が止まる。
    # enable_utf8_output() が効いていることを、実際に出力して確かめる。
    try:
        print("     出力テスト: ¥1,780 / $43.00 / EUR")
        check(True, "金額記号を含む出力")
    except UnicodeEncodeError as e:
        check(False, "金額記号を含む出力", f"{e} — enable_utf8_output() が効いていない")

    # ISO-2022-JP の復号は環境依存で落ちることがある
    try:
        text = "合計 1,780円".encode("iso-2022-jp").decode("iso-2022-jp")
        check(text == "合計 1,780円", "ISO-2022-JP の復号")
    except Exception as e:
        check(False, "ISO-2022-JP の復号", str(e))

    # ---- 経費用途の出力で踏む箇所 ----
    print()
    print("--- 証憑の書き出しで踏む箇所 ---")

    # 請求元名にはファイル名に使えない文字が普通に入る
    dirty = 'ACME: Co/Ltd "特価" <季節>|割引*'
    forbidden = set('\\/:*?"<>|')
    with tempfile.TemporaryDirectory() as tmp:
        raw_ok = True
        try:
            (Path(tmp) / f"2026-01-15_{dirty}_1780.eml").write_bytes(b"x")
        except Exception:
            raw_ok = False
        # ここは OK/NG ではなく観測。どちらに転んでも置換処理は要る。
        print(f"[--] 請求元名をそのままファイル名に: "
              f"{'作れてしまう（表示層で化ける危険）' if raw_ok else '作れない'}"
              f" → 置換処理は必須")

        # 長いパス
        long_name = "あ" * 120
        try:
            (Path(tmp) / f"{long_name}.eml").write_bytes(b"x")
            check(True, "長いファイル名（全角120文字）")
        except Exception as e:
            check(False, "長いファイル名（全角120文字）", f"{type(e).__name__}: 短縮処理が必要")

        # 索引 CSV を Excel で開いたときに化けないか
        index = Path(tmp) / "index.csv"
        with open(index, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["取引年月日", "取引先", "取引金額", "ファイル名"])
            w.writerow(["2026-01-15", "株式会社テスト", 1780, "2026-01-15_test_1780.eml"])
        head = index.read_bytes()[:3]
        check(head == b"\xef\xbb\xbf", "索引CSV に BOM が付く（Excel の文字化け対策）")

        with open(index, "rb") as f:
            data = f.read()
        check(b"\r\n" in data, "CSV の改行が CRLF",
              "Excel は LF でも開けるが、CRLF が無難")

    summary()


def summary() -> None:
    ng = [name for ok, name, _ in results if not ok]
    print()
    if ng:
        print(f"{len(ng)} 件が NG:")
        for name in ng:
            print(f"  - {name}")
        print("\nNG の項目は、経費版を作る前に対処を決める。")
        sys.exit(1)
    print(f"{len(results)} 件すべて OK。この環境で動く。")


if __name__ == "__main__":
    main()
