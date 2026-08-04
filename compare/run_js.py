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
import re
import shutil
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'cli'))
from receipt_ledger.console import enable_utf8_output  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"

BEGIN = "=== 照合対象ここから ==="
END = "=== 照合対象ここまで ==="

# 出力層(csvSafe)は解析側から離れた位置にあるので、印を分けて切り出す。
# ここを含めるまで、CSV に出る直前の処理は照合対象の外にあった。
OUT_BEGIN = "=== 出力の照合対象ここから ==="
OUT_END = "=== 出力の照合対象ここまで ==="

# Windows では PATH に入っていないので、実体のパスを直接見る。
CHROME_CANDIDATES = [
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    # Windows（32bit/64bit の両方の Program Files を見る）
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_chrome() -> str:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    sys.exit(
        "Chrome が見つかりません。\n"
        "  Google Chrome / Chromium / Edge のいずれかを入れるか、\n"
        "  CHROME_CANDIDATES にパスを足してください。"
    )


def _slice(source: str, begin: str, end: str) -> str:
    start = source.find(begin)
    stop = source.find(end)
    if start == -1 or stop == -1:
        sys.exit(f"index.html に印が見つかりません（{begin} / {end}）")
    if stop < start:
        sys.exit(f"印の順序が逆になっています（{begin}）")
    # 印の行そのものは含めない
    start = source.index("\n", start) + 1
    return source[start:stop].rsplit("\n", 1)[0]


def extract_core() -> str:
    """index.html から照合対象の JS を切り出す。

    解析側と出力側の2箇所。出力側(csvSafe)は解析側から 300 行ほど離れた
    位置にあり、印を1つにすると間の UI コードまで巻き込むので分けてある。
    """
    source = (ROOT / "index.html").read_bytes().decode("utf-8", errors="strict")
    return _slice(source, BEGIN, END) + "\n" + _slice(source, OUT_BEGIN, OUT_END)


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
    const row = {
      subject, sender: from, merchant, item,
      amount: found.amount, currency: found.currency,
      date: dateOnlyFromHeader(date) ?? "不明",
      // 「そのメールが取引か」の判定。ここを出さないと、
      // 装置は解析結果だけを見て、弾く側の食い違いを見逃す。
      non_transaction: nonTransactionReason(subject, merchant, body, found.amount) ?? "",
    };
    // CSV に書く直前の形。解析結果が同じでも、ここで割れれば
    // 片方の出力だけ数式として実行される。
    row.csv_cells = [row.date, row.merchant, row.item, row.currency,
                     String(row.amount), row.sender, row.subject].map(csvSafe);
    out[name] = row;
  } catch (e) {
    out[name] = { error: String(e) };
  }
}
// 寄せ処理は1通ずつでは判定できない。全件を渡した結果を
// 「どれに寄せられたか」として1件ずつの行に書き戻す。
// こうすると既存の突き合わせ機構がそのまま使えて、装置の守備範囲が広がる。
{
  const names = Object.keys(out).filter(n => out[n] && !out[n].error);
  for (const n of names) out[n].duplicate_of = "";
  const records = names.map(n => Object.assign({ _name: n }, out[n]));
  const { dropped } = collapseDuplicates(records);
  for (const d of dropped) out[d.record._name].duplicate_of = d.kept._name;
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

    読み取りに select を使わないのは、**Windows の select がソケット専用**だからである。
    パイプを渡すと WinError 10093 で落ちる。スレッドで読めば OS を問わない。
    """
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunks: list[bytes] = []
    finished = threading.Event()

    def reader() -> None:
        # read1 は「今あるぶん」を返す。read だと 65536 バイト溜まるまで戻らず、
        # 出力が終わっているのに待ち続ける。
        try:
            while True:
                chunk = proc.stdout.read1(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"</html>" in b"".join(chunks[-2:]):
                    break
        except (OSError, ValueError):
            pass
        finally:
            finished.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    finished.wait(deadline_sec)
    proc.kill()
    proc.wait(timeout=5)
    return b"".join(chunks).decode("utf-8", errors="replace")


def main() -> None:
    enable_utf8_output()
    page = build_page(extract_core())

    # ヘッドレスが動かない環境(CI のサンドボックス等)向けの逃げ道。
    # 出力した HTML を普通のブラウザで開けば、同じ結果が画面に出る。
    if "--emit-page" in sys.argv:
        target = Path(sys.argv[sys.argv.index("--emit-page") + 1])
        target.write_text(page, encoding="utf-8")
        sys.stderr.write(f"ハーネスを書き出しました: {target}\n")
        return

    chrome = find_chrome()

    # ignore_cleanup_errors: --dump-dom が返ったあとも Chrome の子プロセスが
    # プロファイルに書き続けることがあり、後片付けが「Directory not empty」で
    # 落ちる。DOM は取れているのに結果が捨てられるので、片付けの失敗は無視する。
    # macOS では再現せず Linux で出た（GitHub Actions の ubuntu-latest）。
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
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
