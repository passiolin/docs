#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docs 仓库全量规则检查。

用法(在仓库根执行,或任意位置用绝对路径):
    python3 .agents/skills/writing-style/check_docs.py
    python3 .agents/skills/writing-style/check_docs.py --verbose   # 待核项也全量列出

规则来源:同目录 SKILL.md。输出分两类:
    【必修】明确的规范违反,必须修
    【待核】需人工判定,脚本只给线索

设计约束(踩过的坑,别改回去):
  1. 链接解析必须按括号配对扫描,不能用 `\\]\\(([^)]+)\\)` —— 文件名带括号时正则会截断,
     本仓库有 `1.办公网到10.61组网迁移(WG转tailscale).md` 这类文件名。
  2. 表格列数与行内代码判断前必须剥离 `` `...` ``,否则 `kubectl get ep` 之类里的 `|`
     会被当成列分隔符,实测误报率过半。
  3. 空行压缩、行数统计一律排除代码块,围栏内的空行有语义(mermaid、yaml)。
  4. 状态戳只认前 20 行的 blockquote,正文里"并入前实测状态:"不是状态戳。
  5. 同构开场用整句相似度,不要用 n-gram —— 8 字符 n-gram 在长句内大量重叠,实测 568 条噪音。
"""
import os, re, sys, json, difflib
from collections import defaultdict, Counter
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DOCS = os.path.join(ROOT, "docs")
VERBOSE = "--verbose" in sys.argv

if not os.path.isdir(DOCS):
    sys.exit(f"找不到 {DOCS},请确认脚本位于 <repo>/.agents/skills/writing-style/ 下")

must, need = defaultdict(list), defaultdict(list)
def rel(p): return os.path.relpath(p, ROOT)
def add(d, rule, path, line, detail): d[rule].append((rel(path), line, detail))

mds = []
for dirpath, dirnames, filenames in os.walk(DOCS):
    dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", ".obsidian")]
    for f in filenames:
        if f.endswith(".md"):
            mds.append(os.path.join(dirpath, f))
mds.sort()
content = {p: open(p, encoding="utf-8").read() for p in mds}
def L(p): return content[p].split("\n")

def code_block_lines(text):
    """代码围栏内的行号集合(1-based),含围栏行本身"""
    out, inf = set(), False
    for i, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith(("```", "~~~")):
            inf = not inf; out.add(i); continue
        if inf: out.add(i)
    return out

def code_free(line):
    """剥离行内代码,保留其余文本"""
    return re.sub(r"`[^`\n]*`", "", line)

def prose_only(text):
    """去掉代码块/表格/引用/标题/链接后的散文行,用于同构与重复检测"""
    out, inf = [], False
    for i, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith(("```", "~~~")):
            inf = not inf; continue
        if inf: continue
        s = line.strip()
        if not s or s.startswith(("|", "<!--", ">", "#", "---")):
            continue
        s = re.sub(r"`[^`\n]*`", "", s)
        s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
        out.append((i, s))
    return out

# ==================== A 目录与命名 ====================
theme_dirs = []
for dirpath, dirnames, filenames in os.walk(DOCS):
    dirnames[:] = [d for d in dirnames if d not in (".git",)]
    r = os.path.relpath(dirpath, DOCS)
    if r != "." and r.count(os.sep) == 1:
        theme_dirs.append(dirpath)

def is_skill_app(d):
    """skill 应用目录自带 SKILL.md 入口,不适用「README 当索引」"""
    return os.path.exists(os.path.join(d, "SKILL.md"))

REPORT_NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*-\d{8}\.md$")  # 类型-YYYYMMDD.md

for d in sorted(theme_dirs):
    if is_skill_app(d):
        continue
    names = sorted(os.listdir(d))
    arts = [n for n in names if n.endswith(".md") and n != "README.md" and re.match(r"^\d+\.", n)]
    others = [n for n in names if n.endswith(".md") and n not in arts and n != "README.md"]
    if not os.path.exists(os.path.join(d, "README.md")):
        add(must, "A1-缺目录索引", d, 0, f"{rel(d)} 无 README.md(编号文章 {len(arts)} 篇)")
    for n in others:
        # reports/ 下按「类型-YYYYMMDD.md」命名,是报告不是文章
        if os.path.basename(d) == "reports" and REPORT_NAME.match(n):
            continue
        add(must, "A2-文章无编号", os.path.join(d, n), 0, f"{n} 不符 N.标题.md")
    if arts:
        nums = sorted(int(re.match(r"^(\d+)\.", n).group(1)) for n in arts)
        for num, cnt in sorted(Counter(nums).items()):
            if cnt > 1:
                add(must, "A3-编号重复", d, 0, f"编号 {num} 出现 {cnt} 次")
        gap = [n for n in range(1, max(nums) + 1) if n not in nums]
        if gap:
            add(must, "A3-编号不连续", d, 0, f"实际 {nums},缺 {gap}")

for lvl1 in sorted(os.listdir(DOCS)):
    p1 = os.path.join(DOCS, lvl1)
    if not os.path.isdir(p1):
        continue
    for e in sorted(os.listdir(p1)):
        p = os.path.join(p1, e)
        if os.path.isfile(p) and e.endswith(".md") and e != "README.md":
            add(must, "A4-文章未归主题目录", p, 0, f"{rel(p)} 直接位于一级目录下")

# ==================== B 链接 ====================
def find_links(line):
    """从左到右扫描 `](`,内部按括号配对。返回 [(raw_target, is_image, col)]"""
    out, i, n = [], 0, len(line)
    while i < n:
        j = line.find("](", i)
        if j == -1:
            break
        k = j
        while k >= 0 and line[k] != "[":
            k -= 1
        if k < 0:
            i = j + 2; continue
        is_img = k > 0 and line[k - 1] == "!"
        m, depth = j + 2, 1
        while m < n and depth > 0:
            ch = line[m]
            if ch == "\\":
                m += 2; continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            m += 1
        if depth != 0:
            i = j + 2; continue
        raw = re.sub(r'\s+"[^"]*"$', "", line[j + 2:m - 1]).strip()
        if raw.startswith("<") and raw.endswith(">"):
            raw = raw[1:-1]
        out.append((raw, is_img, j))
        i = m
    return out

all_links = []
for p in mds:
    cb = code_block_lines(content[p])
    for lineno, line in enumerate(L(p), 1):
        if lineno in cb:
            continue
        for raw, is_img, _ in find_links(line):
            all_links.append((p, lineno, raw, is_img))

for p, lineno, tgt, is_img in all_links:
    if re.match(r"^(https?:|mailto:|tel:|data:|ftp:)", tgt) or tgt.startswith("#"):
        continue
    if tgt.startswith("/"):
        add(must, "B1-站内根路径", p, lineno, tgt); continue
    plain = unquote(tgt.split("#")[0].split("?")[0])
    if not plain:
        continue
    if plain.startswith(("~", "/home/")):
        add(must, "B2-本机绝对路径", p, lineno, tgt); continue
    tp = os.path.normpath(os.path.join(os.path.dirname(p), plain))
    if not os.path.exists(tp):
        add(must, "B3-图片断链" if is_img else "B3-链接断链", p, lineno, f"{tgt} → {rel(tp)}")

for p in mds:
    cb = code_block_lines(content[p])
    for lineno, line in enumerate(L(p), 1):
        if lineno in cb:
            continue
        if re.search(r"/home/passio|/Users/passio", line):
            add(must, "B2-本机绝对路径", p, lineno, line.strip()[:110])

# ==================== C 人称 ====================
EXT_AUTHOR = re.compile(r"(Jay Kreps|Doug Cutting|antirez|作者之子|三位作者|原作者|模型作者|项目作者|上游作者|作者名)")
QUOTED = re.compile(r"[\"“「『][^\"”」』]{0,50}[\"”」』]")

for p in mds:
    cb = code_block_lines(content[p])
    for lineno, line in enumerate(L(p), 1):
        if lineno in cb:
            continue
        cl = code_free(line)
        if re.search(r"作者|本人", cl) and not EXT_AUTHOR.search(cl):
            add(must, "C1-旁观者自称", p, lineno, cl.strip()[:120])
        if "笔者" in cl and "我们" in cl:
            add(need, "C2-同句人称混用", p, lineno, cl.strip()[:120])
        # 引号外单独出现的"我"=自指,该用"笔者";首页自我介绍豁免
        if not p.endswith(os.path.join("docs", "README.md")):
            for mt in re.finditer(r"(?<![我你他她它])我(?!们)", QUOTED.sub("", cl)):
                add(must, "C3-第一人称用'我'", p, lineno, cl[max(0, mt.start()-14):mt.start()+14].strip())
        # 引号内的"我"=给技术对象派人格,交人工判断
        for qt in QUOTED.finditer(cl):
            if re.search(r"(?<![我你他她它])我(?!们)", qt.group(0)):
                add(need, "C4-引号内拟人", p, lineno, cl.strip()[:110])

# ==================== D AI 腔 ====================
META = ["本节先", "本章讲", "本章将", "本章回答", "下面我们来看", "先铺", "再交代",
        "值得注意的是", "需要关注的是", "总而言之", "综上所述", "不难发现", "由此可见",
        "众所周知", "需要指出的是", "本节将", "本文将", "我们来看", "先说说", "先看看",
        "如你所见", "让我们", "接下来我们"]
STYLE = ["退一步", "诚然", "不可否认", "担忧不成立", "换个角度", "某种意义上", "某种程度上",
         "说白了", "不得不说", "毫无疑问", "可以想象", "试想", "反过来想", "换句话说",
         "一目了然", "不言而喻"]
VAGUE = ["收益显著", "效果显著", "非常重要", "至关重要", "极其重要", "意义重大",
         "大幅提升", "质的飞跃", "完美解决", "彻底解决", "一劳永逸", "显而易见", "极为重要"]

for p in mds:
    cb = code_block_lines(content[p])
    for lineno, line in enumerate(L(p), 1):
        if lineno in cb:
            continue
        cl = code_free(line)
        for kw in META:
            if kw in cl:
                add(must, "D1-元叙述空洞词", p, lineno, f"[{kw}] {cl.strip()[:110]}"); break
        for kw in STYLE:
            if kw in cl:
                add(must, "D2-议论文转折", p, lineno, f"[{kw}] {cl.strip()[:110]}"); break
        for kw in VAGUE:
            if kw in cl:
                add(need, "D5-空泛断言", p, lineno, f"[{kw}] {cl.strip()[:110]}"); break

for p in mds:
    for i, line in enumerate(L(p)[:20], 1):
        if line.lstrip().startswith(">") and re.search(r"实测状态|验证状态|状态[:：]\s*✅|✅\s*已实测", line):
            add(must, "D3-状态戳", p, i, line.strip()[:120])

for p in mds:
    for kw in ["以实测为准", "未验证标 TODO", "仅供参考", "不构成建议", "有待验证"]:
        n = content[p].count(kw)
        if n > 1:
            add(must, "D4-免责复读", p, 0, f"「{kw}」出现 {n} 次")

# 否定铺垫:两端都带具体名词(数字/英文术语)算合法对比,否则交人工
for p in mds:
    cb = code_block_lines(content[p])
    for lineno, line in enumerate(L(p), 1):
        if lineno in cb:
            continue
        cl = code_free(line)
        for mt in re.finditer(r"不是([^,。;]{1,24})[,，]?\s*而是([^,。;]{1,24})", cl):
            a, b = mt.group(1), mt.group(2)
            if re.search(r"[0-9]|[A-Za-z]{3,}", a) or re.search(r"[0-9]|[A-Za-z]{3,}", b):
                continue
            add(need, "D6-否定铺垫", p, lineno, cl.strip()[:120])

# ==================== E 结构 ====================
for p in mds:
    ls = L(p)
    fences = [i for i, l in enumerate(ls, 1) if l.lstrip().startswith("```")]
    if len(fences) % 2:
        add(must, "E1-代码围栏未闭合", p, fences[-1] if fences else 0, f"{len(fences)} 个围栏")

    prev, cb = 0, code_block_lines(content[p])
    for lineno, line in enumerate(ls, 1):
        if lineno in cb:
            continue
        h = re.match(r"^(#{1,6})\s+", line)
        if h:
            lvl = len(h.group(1))
            if prev and lvl > prev + 1:
                add(must, "E2-标题层级跳跃", p, lineno, f"H{prev}→H{lvl} {line.strip()[:60]}")
            prev = lvl

    def flush(tbl):
        if len(tbl) >= 3:
            main = Counter(x for _, x in tbl).most_common(1)[0][0]
            for ln, x in tbl:
                if x != main:
                    add(must, "E3-表格列数不齐", p, ln, f"{x-1} 列 vs 表体 {main-1} 列")

    tbl = []
    for lineno, line in enumerate(ls, 1):
        if lineno in cb:
            flush(tbl); tbl = []; continue
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            tbl.append((lineno, code_free(s).count("|")))
        else:
            flush(tbl); tbl = []
    flush(tbl)

    for t, n in Counter(l.strip() for l in ls if re.match(r"^##\s+", l)).items():
        if n > 1:
            add(must, "E4-文档内标题重复", p, 0, f"{t[:60]} ×{n}")

# ==================== F 索引 ====================
for d in sorted(theme_dirs):
    if is_skill_app(d):
        continue
    rd = os.path.join(d, "README.md")
    if not os.path.exists(rd):
        continue
    txt = content[rd]
    arts = [n for n in sorted(os.listdir(d)) if n.endswith(".md") and n != "README.md" and re.match(r"^\d+\.", n)]
    if arts and not re.search(r"^\|\s*编号\s*\|", txt, re.M):
        add(must, "F0-索引无编号表", rd, 0, f"{rel(d)} 有 {len(arts)} 篇编号文章,索引缺「编号|文章|内容|状态」表")
    for a in arts:
        if a not in txt and a[:-3] not in txt:
            add(must, "F1-索引漏项", rd, 0, f"{a} 未在索引中出现")
    for raw, is_img, _ in find_links(txt):
        if re.match(r"^(https?:|#|/)", raw):
            continue
        t = unquote(raw.split("#")[0])
        if t.endswith(".md") and not os.path.exists(os.path.normpath(os.path.join(d, t))):
            add(must, "F2-索引死链", rd, 0, raw)

# 首页是个人主页,索引的是主题目录而非一级目录,一级目录缺席不算问题
rr = os.path.join(DOCS, "README.md")
if os.path.exists(rr):
    txt = content[rr]
    for e in sorted(os.listdir(DOCS)):
        if os.path.isdir(os.path.join(DOCS, e)) and e not in txt:
            add(need, "F3-首页未提一级目录", rr, 0, f"docs/{e} 未在首页出现(首页若只索引主题目录可忽略)")

# ==================== G 孤儿 ====================
linked = set()
for p, lineno, raw, is_img in all_links:
    if re.match(r"^(https?:|#|/)", raw):
        continue
    t = unquote(raw.split("#")[0].split("?")[0])
    if t:
        linked.add(os.path.normpath(os.path.join(os.path.dirname(p), t)))
for p in mds:
    if os.path.basename(p) == "README.md" or p in linked:
        continue
    rd = os.path.join(os.path.dirname(p), "README.md")
    base = os.path.basename(p)
    if rd in content and (base in content[rd] or base[:-3] in content[rd]):
        continue
    add(must, "G1-孤儿文档", p, 0, f"无人链接 {rel(p)}")

# ==================== I 同构开场 ====================
def norm(s):
    s = re.sub(r"[`*_\[\]()（）,，。.;；:：!！?？\-—…、/\"“”'']", "", s)
    return re.sub(r"\s+", "", s)

openers = {}
for p in mds:
    for lineno, line in prose_only(content[p]):
        if len(line) >= 15:
            openers[p] = norm(line)[:40]
            break
pl = sorted(openers)
seen = set()
for i in range(len(pl)):
    for j in range(i + 1, len(pl)):
        a, b = pl[i], pl[j]
        if len(openers[a]) < 12 or len(openers[b]) < 12:
            continue
        r = difflib.SequenceMatcher(None, openers[a], openers[b]).ratio()
        if r > 0.62 and (a, b) not in seen:
            seen.add((a, b))
            add(need, "I1-同构开场", a, 0,
                f"与 {rel(b)} 相似 {r:.0%}({openers[a][:24]} / {openers[b][:24]})")

# ==================== J 内容重复 ====================
def shingles(p, k=50):
    t = re.sub(r"\s+", "", "".join(l for _, l in prose_only(content[p])))
    return {t[i:i+k] for i in range(0, max(0, len(t)-k), 4)}

shs = {p: shingles(p) for p in mds}
for i in range(len(mds)):
    for j in range(i + 1, len(mds)):
        a, b = mds[i], mds[j]
        if len(shs[a]) < 20 or len(shs[b]) < 20:
            continue
        inter = len(shs[a] & shs[b])
        if inter < 15:
            continue
        jac = inter / len(shs[a] | shs[b])
        if jac > 0.10:
            add(need, "J1-内容重复", a, 0, f"与 {rel(b)} 重合 {jac:.0%}")

# ==================== K 标题一致性 ====================
for p in mds:
    h1 = next((l for l in L(p) if l.startswith("# ")), None)
    if not h1:
        add(must, "K1-缺一级标题", p, 0, os.path.basename(p))
        continue
    hm = re.match(r"^(\d+)\.(.+)\.md$", os.path.basename(p))
    if not hm:
        continue
    t = re.sub(r"^\d+[.、]\s*", "", h1[2:]).strip()
    def toks(s):
        s = re.sub(r"[（()）:：\-—_ ]", "", s)
        return set(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fa5]{2,}", s))
    ta, tb = toks(hm.group(2)), toks(t)
    if ta and tb and len(ta & tb) / min(len(ta), len(tb)) < 0.5:
        add(need, "K2-标题与文件名差异", p, 1, f"文件「{hm.group(2)[:34]}」/ 标题「{t[:34]}」")

# ==================== H 卫生 ====================
for p in mds:
    ls, cb = L(p), code_block_lines(content[p])
    for lineno, line in enumerate(ls, 1):
        if lineno not in cb and re.search(r"[ \t]+$", line):
            add(must, "H1-行尾空格", p, lineno, repr(line[-40:]))
    if content[p] and not content[p].endswith("\n"):
        add(must, "H3-末尾无换行", p, 0, "")
    # 连续空行只在代码块外算
    run = 0
    for lineno, line in enumerate(ls, 1):
        if lineno in cb:
            run = 0; continue
        run = run + 1 if line.strip() == "" else 0
        if run == 4:
            add(must, "H4-连续空行>3", p, lineno, "")
    for lineno, line in enumerate(ls, 1):
        if re.search(r"\bTODO\b|\bFIXME\b|待补|待填|待测|待验证", line):
            add(need, "H5-未完成标记", p, lineno, line.strip()[:100])

# ==================== 输出 ====================
def dump(title, d):
    tot = sum(len(v) for v in d.values())
    print(f"\n{'=' * 72}\n{title}  共 {tot} 项\n{'=' * 72}")
    if tot == 0:
        print("  (无)")
    for k in sorted(d):
        v = d[k]
        print(f"\n### {k}  ({len(v)})")
        lim = len(v) if VERBOSE else 60
        for path, line, detail in v[:lim]:
            print(f"  {path}:{line}  {detail}" if line else f"  {path}  {detail}")
        if len(v) > lim:
            print(f"  ... 另有 {len(v)-lim} 项(--verbose 全量)")

print(f"仓库 {ROOT}\n扫描 {len(mds)} 篇 markdown")
dump("【必修】明确的规范违反", must)
dump("【待核】需人工判定", need)

if not VERBOSE and sum(len(v) for v in must.values()) == 0:
    print("\n必修项清零。")
