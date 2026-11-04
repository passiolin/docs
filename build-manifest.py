#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描当前目录，生成 manifest.json（目录树）与 aliases.json（短链接别名表）。

用法：
    cd 你的markdown根目录
    python3 build-manifest.py

新增 / 删除文档后重新执行一次即可。

aliases.json 的生成规则：
  每篇文档一个短别名，取相对路径 md5 的前 16 位。
  哈希整条相对路径而不是文件名——README.md 这类同名文件在多个目录下都有，
  只哈希文件名会撞成同一个别名。文档改名后别名随之变化，旧短链失效，
  不需要再手工维护别名表。
"""
import hashlib
import json
import os
import re
import sys

# ---- 排除规则（每一段单独匹配，可自行增删） ------------------------
EXCLUDE_FILES = {"index.html", "manifest.json", "aliases.json", "build-manifest.py",
                 "desktop.ini", "thumbs.db", ".ds_store"}
EXCLUDE_DIRS = {"assets", "node_modules", ".git", ".idea", ".vscode", "__pycache__"}

# 与 index.html 的 CONFIG.DOC_ROOT 保持一致；短别名只要求覆盖这个目录下的文档
DOC_ROOT = "docs"


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


# ---- 短别名生成 ----------------------------------------------------

def collect_md_files():
    """DOC_ROOT 下所有 .md 的相对路径集合（与目录树同一套排除规则）。"""
    md_files = set()
    base = DOC_ROOT if DOC_ROOT else "."
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in EXCLUDE_DIRS]
        for name in files:
            if name.lower().endswith(".md") and name not in EXCLUDE_FILES:
                md_files.add(os.path.relpath(os.path.join(root, name)).replace(os.sep, "/"))
    return md_files


def alias_for(rel: str) -> str:
    """短别名 = 相对路径 md5 的前 16 位。"""
    return hashlib.md5(rel.encode("utf-8")).hexdigest()[:16]


def build_aliases():
    """生成 aliases.json：每篇文档一个由路径派生的固定长度别名。"""
    md_files = collect_md_files()
    aliases = {}
    for t in sorted(md_files):
        base = alias_for(t)
        a, n = base, 2
        while a in aliases:
            a = f"{base}-{n}"
            n += 1
        aliases[a] = t

    with open("aliases.json", "w", encoding="utf-8") as f:
        json.dump(dict(sorted(aliases.items())), f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"✅ 已生成 aliases.json：{len(aliases)} 个别名（md5 路径派生）")


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
    build_aliases()


if __name__ == "__main__":
    main()
