"""ブラウザ実装と Python 実装の結果を1件ずつ突き合わせる。

    python3 compare/compare.py

差分があれば終了コード 1。変更のたびに回すための装置なので、
「たまたま今日は合っていた」ではなく、合わなくなった瞬間に気づけることを目的にする。

ヘッドレスブラウザが使えない環境では、JS 側の結果を別に用意して渡す:

    python3 compare/run_js.py --emit-page /tmp/harness.html   # ブラウザで開く
    python3 compare/compare.py --js /tmp/result.txt           # 画面の文字列を保存して渡す

--js は JSON でも、ハーネスの画面に出る base64 でも受ける。
デコードを手作業にすると、この逃げ道は使われなくなるため。
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
FIELDS = ["subject", "sender", "merchant", "item", "amount", "currency", "date"]


def load_js() -> dict:
    if "--js" in sys.argv:
        text = Path(sys.argv[sys.argv.index("--js") + 1]).read_text(encoding="utf-8").strip()
        # ハーネスを手でブラウザに開いた場合、画面に出るのは base64。
        # デコードを手作業にすると使われなくなるので、どちらでも受ける。
        if not text.startswith("{"):
            text = base64.b64decode(text).decode("utf-8")
        return json.loads(text)
    proc = subprocess.run(
        [sys.executable, str(HERE / "run_js.py")], capture_output=True, text=True
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        sys.exit("JS 側の実行に失敗しました")
    return json.loads(proc.stdout)


def load_py() -> dict:
    proc = subprocess.run(
        [sys.executable, str(HERE / "run_py.py")], capture_output=True, text=True
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        sys.exit("Python 側の実行に失敗しました")
    return json.loads(proc.stdout)


def diff_case(js: dict | None, py: dict | None) -> list[tuple[str, object, object]]:
    """片方だけが「レシートではない」と判断した場合も差分として扱う。"""
    if js is None or py is None:
        if js is None and py is None:
            return []
        return [("(レコードの有無)", "なし" if js is None else "あり", "なし" if py is None else "あり")]
    out = []
    for field in FIELDS:
        a, b = js.get(field), py.get(field)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(a - b) > 1e-9:
                out.append((field, a, b))
        elif a != b:
            out.append((field, a, b))
    return out


def main() -> None:
    js, py = load_js(), load_py()

    names = sorted(set(js) | set(py))
    diffs = 0
    for name in names:
        rows = diff_case(js.get(name), py.get(name))
        if not rows:
            print(f"一致  {name}")
            continue
        diffs += 1
        print(f"差分  {name}")
        for field, a, b in rows:
            print(f"        {field}")
            print(f"          ブラウザ: {a!r}")
            print(f"          Python  : {b!r}")

    print()
    print(f"{len(names)} 件中 {diffs} 件が食い違い")
    sys.exit(1 if diffs else 0)


if __name__ == "__main__":
    main()
