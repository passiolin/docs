#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描当前目录，生成 manifest.json，供 index.html 构建左侧目录树。

用法：
    cd 你的markdown根目录
    python3 build-manifest.py

新增 / 删除文档后重新执行一次即可。
"""
import json
import os
import re
import sys

# ---- 排除规则（每一段单独匹配，可自行增删） ------------------------
EXCLUDE_FILES = {"index.html", "manifest.json", "build-manifest.py",
                 "desktop.ini", "thumbs.db", ".ds_store"}
EXCLUDE_DIRS = {"assets", "node_modules", ".git", ".idea", ".vscode", "__pycache__"}


def natural_key(name: str):
    """自然排序：page2 排在 page10 前面，中文按拼音/Unicode 排序。"""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name.lower())]


def sort_children(children):
    """目录在前；文件中 README.md 置顶；其余自然排序。"""
    children.sort(
        key=lambda n: (
            0 if n["type"] == "dir" else 1,
            0 if (n["type"] == "file" and n["name"].lower() == "readme.md") else 1,
            natural_key(n["name"]),
        )
    )
    return children


def scan(path: str, rel: str = ""):
    """递归扫描，返回树节点；空目录被剪掉。"""
    name = os.path.basename(path) if rel else ""
    children = []
    try:
        entries = sorted(os.listdir(path))
    except OSError as e:
        print(f"跳过不可读目录 {path}: {e}", file=sys.stderr)
        return None

    for entry in entries:
        if entry.startswith("."):
            continue
        full = os.path.join(path, entry)
        if os.path.isdir(full):
            if entry in EXCLUDE_DIRS:
                continue
            child = scan(full, f"{rel}/{entry}" if rel else entry)
            if child and child["children"]:
                children.append(child)
        else:
            if entry in EXCLUDE_FILES:
                continue
            children.append(
                {"type": "file", "name": entry, "path": f"{rel}/{entry}" if rel else entry}
            )

    return {"type": "dir", "name": name, "path": rel, "children": sort_children(children)}


def main():
    root_name = os.path.basename(os.path.abspath(os.getcwd()))
    tree = scan(os.getcwd())
    if not tree or not tree["children"]:
        print("没有找到任何 .md 文件，未生成 manifest.json")
        return

    tree["name"] = root_name
    count = 0

    def count_files(node):
        nonlocal count
        if node["type"] == "file":
            count += 1
            return
        for c in node["children"]:
            count_files(c)

    count_files(tree)

    # index.html 不需要目录信息，输出精简的树
    with open("manifest.json", "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False, separators=(",", ":"))

    print(f"✅ 已生成 manifest.json：{count} 个文件，{len(tree['children'])} 个顶层条目")
    print("   刷新浏览器即可看到最新目录。")


if __name__ == "__main__":
    main()
