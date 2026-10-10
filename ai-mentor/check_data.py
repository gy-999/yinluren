# -*- coding: utf-8 -*-
"""引路人 · 内容文件校验

    python check_data.py

改完知识库 / FAQ / 关卡地图后用这个脚本检查一遍，比刷新页面发现问题更快。
有"错误"时退出码为 1（可用于提交前钩子）。

为什么要单独做这个：内容文件是给人手改的。JSON 里少一个逗号，整个文件会被忽略，
工具不会崩，只会"静默变傻"——那比崩溃更危险。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 输出被重定向（管道 / CI / 钩子）时 Python 退回本地编码（中文 Windows 是 GBK），强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (ValueError, OSError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

import core  # noqa: E402

ICON = {"error": "错误", "warn": "提醒"}


def main() -> int:
    issues = core.validate_data()
    errs = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]

    print("=" * 62)
    print("  内容文件校验")
    print("=" * 62)
    print(f"  知识条目：{len(core.KB.entries)}")
    print(f"  FAQ：{len([i for i in core.KB.faq['items'] if i.get('status') == 'published'])} 条已发布 / "
          f"{len([i for i in core.KB.faq['items'] if i.get('status') == 'draft'])} 条草稿")
    print(f"  检索索引：{len(core.KB.index.vectors) if core.KB.index else 0} 个段落向量")
    print()

    if not issues:
        print("  全部通过，没有发现问题。")
        print("=" * 62)
        return 0

    for level in ("error", "warn"):
        group = errs if level == "error" else warns
        if not group:
            continue
        print(f"  [{ICON[level]}] {len(group)} 条")
        for i in group:
            print(f"    · {i['where']}")
            print(f"      {i['message']}")
        print()

    print("=" * 62)
    print(f"  合计：{len(errs)} 个错误 / {len(warns)} 个提醒")
    if errs:
        print("  有错误的文件会被整体忽略，请优先修掉。")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
