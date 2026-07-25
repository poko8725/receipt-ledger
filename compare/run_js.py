"""ブラウザ実装(index.html)に fixtures を流し、比較用の JSON を吐く。

    python3 compare/run_js.py > /tmp/js.json

## なぜ Node ではなくブラウザで動かすのか

解析コードは `DOMParser`(実体参照のデコード)と `TextDecoder`(文字コード)に依存している。
Node には DOMParser が無いので、代わりの実装を差し込むことになるが、
**それをやると照合しているのは本物の経路ではなく差し込んだ実装のほうになる**。
壊れ方は差し込んだ場所に出るので、これでは装置として意味がない。

そこで手元にある Chrome をヘッドレスで起動し、実際の実行環境で動かす。
追加インストールは無い。

## どうやって本物のコードを取り出すか

index.html を書き写すと三つ目の実装が生まれ、照合の意味が消える。
`=== 照合対象ここから ===` から `=== 照合対象ここまで ===` までを実行時に切り出し、
そのまま使う。印を動かすかコードを移動すればここで落ちる。
"""

from __future__ import annotations

import base64
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"

BEGIN = "=== 照合対象ここから ==="
END = "=== 照合対象ここまで ==="

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def find_chrome() -> str:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    sys.exit(
        "Chrome が見つかりません。\n"
        "  Google Chrome / Chromium / Edge のいずれかを入れるか、\n"
        "  CHROME_CANDIDATES にパスを足してください。"
    )


def extract_core() -> str:
    """index.html から照合対象の JS を切り出す。"""
    source = (ROOT / "index.html").read_bytes().decode("utf-8", errors="strict")
    start = source.find(BEGIN)
    end = source.find(END)
    if start == -1 or end == -1:
        sys.exit(f"index.html に印が見つかりません（{BEGIN} / {END}）")
    if end < start:
        sys.exit("印の順序が逆になっています")
    # 印の行そのものは含めない
    start = source.index("\n", start) + 1
    return source[start:end].rsplit("\n", 1)[0]


def build_page(core: str) -> str:
    paths = sorted(FIXTURES.glob("*.eml"))
    if not paths:
        # フィクスチャは生成物なのでリポジトリに入っていない(.gitignore の *.eml)。
        # 無いまま走ると差分ゼロで通ってしまうので、ここで止める。
        sys.exit(
            "フィクスチャがありません。先に生成してください:\n"
            "    python3 compare/make_fixtures.py"
        )
    cases = {p.name: base64.b64encode(p.read_bytes()).decode() for p in paths}
    runner = """
const CASES = __CASES__;
const out = {};
for (const [name, b64] of Object.entries(CASES)) {
  try {
    // readFileAsBinaryString と同じ形(1バイト=1文字)にする
    const raw = atob(b64);
    const { subject, from, date, body } = parseEml(raw);
    const found = extractAmount(subject) ?? extractAmount(body);
    if (found === null) { out[name] = null; continue; }
    const { merchant, item } = resolveMerchant(from, subject, body);
    out[name] = {
      subject, sender: from, merchant, item,
      amount: found.amount, currency: found.currency,
      date: dateOnlyFromHeader(date) ?? "不明",
    };
  } catch (e) {
    out[name] = { error: String(e) };
  }
}
// DOM に日本語をそのまま置くと dump-dom で実体参照に化けるので base64 で運ぶ
const json = JSON.stringify(out);
const bytes = new TextEncoder().encode(json);
let bin = "";
for (const b of bytes) bin += String.fromCharCode(b);
document.title = "done";
document.body.textContent = btoa(bin);
"""
    runner = runner.replace("__CASES__", json.dumps(cases))
    return (
        "<!DOCTYPE html><html><head><meta charset='UTF-8'><title>compare</title></head>"
        "<body></body><script>\n" + core + "\n" + runner + "\n</script></html>"
    )


def read_dom(command: list[str], deadline_sec: float = 60.0) -> str:
    """DOM を吐かせて読み取る。プロセスの終了は待たない。

    `--dump-dom` は DOM を標準出力に書いたあとも、Chrome が常駐して終了しないことがある
    (自動更新のプロセスが上がるなど)。終了を待つ実装にすると、
    **結果は既に手元にあるのに必ずタイムアウトする**。
    出力の終わりは終了コードではなく `</html>` で判定する。
    """
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buffer = b""
    limit = time.time() + deadline_sec
    try:
        while time.time() < limit:
            ready, _, _ = select.select([proc.stdout], [], [], 1.0)
            if ready:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                buffer += chunk
                if b"</html>" in buffer:
                    break
            elif proc.poll() is not None:
                break
    finally:
        proc.kill()
        proc.wait(timeout=5)
    return buffer.decode("utf-8", errors="replace")


def main() -> None:
    page = build_page(extract_core())

    # ヘッドレスが動かない環境(CI のサンドボックス等)向けの逃げ道。
    # 出力した HTML を普通のブラウザで開けば、同じ結果が画面に出る。
    if "--emit-page" in sys.argv:
        target = Path(sys.argv[sys.argv.index("--emit-page") + 1])
        target.write_text(page, encoding="utf-8")
        sys.stderr.write(f"ハーネスを書き出しました: {target}\n")
        return

    chrome = find_chrome()

    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "harness.html"
        html.write_text(page, encoding="utf-8")
        # 起動中の Chrome と衝突しないよう、使い捨てのプロファイルで動かす。
        command = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            f"--user-data-dir={tmp}/profile",
            "--dump-dom",
            html.as_uri(),
        ]
        dumped = read_dom(command)

    match = re.search(r"<body>([A-Za-z0-9+/=\s]*)</body>", dumped)
    if not match or not match.group(1).strip():
        keep = Path(tempfile.gettempdir()) / "receipt-ledger-harness.html"
        keep.write_text(page, encoding="utf-8")
        sys.stderr.write(dumped[-2000:] + "\n")
        sys.exit(
            "ブラウザから結果を取り出せませんでした。\n"
            f"  同じページを手で開いて確認する: open {keep}\n"
            "  画面に出る文字列を保存して: python3 compare/compare.py --js <保存したファイル>\n"
        )

    payload = base64.b64decode(match.group(1).strip()).decode("utf-8")
    json.dump(json.loads(payload), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
