"""コンソール出力の文字コードを揃える。

Windows の既定コンソールは cp932 で、`¥`(U+00A5) も `-`(em dash, U+2014) も encode できない。
このツールの出力には金額が必ず含まれるので、**表示の一箇所で UnicodeEncodeError を出して
処理全体が止まる**。しかも落ちるのは解析ではなく print なので、
「Windows では動かない」という誤った結論になりやすい。

実測では、判定スクリプトの1件目の表示で落ち、以降が一切実行されなかった。
原因は ISO-2022-JP でも解析ロジックでもなく、書式文字列に直書きされた em dash だった。

対処は二段構え:

1. 出力ストリームを UTF-8 に寄せる（この関数）
2. 装飾目的の非 ASCII 文字を使わない（em dash ではなく ASCII の "-" を書く）

1 だけでは足りない。この関数を通らない出力経路が残ると同じことが起きる。
2 だけでも足りない。`¥` は意味を持つ文字なので置換できない。
"""

from __future__ import annotations

import sys


def enable_utf8_output() -> None:
    """標準出力・標準エラーを UTF-8 にする。実行の入口で1回呼ぶ。

    errors="replace" は保険。化けて表示されるほうが、落ちて何も出ないよりましである。
    reconfigure が無い環境や差し替えられたストリームでは、黙って諦める。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
