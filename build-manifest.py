#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描当前目录，生成 manifest.json（目录树）与 aliases.json（短链接别名表）。

用法：
    cd 你的markdown根目录
    python3 build-manifest.py

新增 / 删除文档后重新执行一次即可。

aliases.json 的生成规则：
  1. 保留现有 aliases.json 中仍然有效的手改条目（手改优先）；
  2. 补上内置种子 ALIAS_SEED 里目标存在的别名；
  3. 剩下没别名的文档，从路径里的 ASCII 词自动派生（如 mysql/5.MGR集群.md → mysql/mgr），
     撞名自动加 -2、-3 后缀；完全抽不出词的用 p-xxxxxx 兜底。
每次运行都是全量重建，手工增删的别名只要目标文件还在就会保留。
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

# ---- 内置短别名种子（alias → 相对路径） ----------------------------
# 人工审定过的常用别名，生成时优先采用；文档改名/删除后由生成流程自动清理
ALIAS_SEED = {
    "doris": "docs/部署相关/doris/README.md",
    "doris/docker": "docs/部署相关/doris/2.架构与部署-docker.md",
    "doris/ha-scale": "docs/部署相关/doris/4.高可用与扩缩容.md",
    "doris/import-query": "docs/部署相关/doris/3.数据导入与查询.md",
    "doris/install": "docs/部署相关/doris/1.安装部署-单机.md",
    "doris/ops": "docs/部署相关/doris/5.监控与日常运维.md",
    "es": "docs/部署相关/elasticsearch/README.md",
    "es/8.19.14": "docs/部署相关/elasticsearch/8.19.14/README.md",
    "es/baseline": "docs/部署相关/elasticsearch/3.参数基线与配置模板.md",
    "es/cluster": "docs/部署相关/elasticsearch/2.安装部署-三节点.md",
    "es/dr": "docs/部署相关/elasticsearch/6.容灾实测.md",
    "es/index-search": "docs/部署相关/elasticsearch/4.索引与检索.md",
    "es/install": "docs/部署相关/elasticsearch/1.安装部署-单机.md",
    "es/monitoring": "docs/部署相关/elasticsearch/8.监控与告警.md",
    "es/ops": "docs/部署相关/elasticsearch/7.日常运维与排障.md",
    "es/snapshot": "docs/部署相关/elasticsearch/5.快照备份.md",
    "fcm": "docs/技术方案/FCM推送接入/README.md",
    "fcm/firebase": "docs/技术方案/FCM推送接入/2.Firebase云端配置指南.md",
    "fcm/solution": "docs/技术方案/FCM推送接入/1.FCM消息推送技术方案.md",
    "filebeat": "docs/部署相关/filebeat/README.md",
    "home": "docs/README.md",
    "jenkins": "docs/部署相关/jenkins/master/README.md",
    "jenkins/node-java": "docs/部署相关/jenkins/slave/java/README.md",
    "jenkins/node-npm": "docs/部署相关/jenkins/slave/npm/README.md",
    "jenkins/node-python": "docs/部署相关/jenkins/slave/python/README.md",
    "jmqtt": "docs/部署相关/jmqtt/README.md",
    "jmqtt/admin": "docs/部署相关/jmqtt/5.jmqtt-admin.md",
    "jmqtt/cluster": "docs/部署相关/jmqtt/4.集群部署.md",
    "jmqtt/config": "docs/部署相关/jmqtt/2.参数与配置.md",
    "jmqtt/feature-test": "docs/部署相关/jmqtt/3.MQTT功能矩阵实测.md",
    "jmqtt/install": "docs/部署相关/jmqtt/1.安装部署-单机.md",
    "jmqtt/ops": "docs/部署相关/jmqtt/6.日常运维与排障.md",
    "k3s": "docs/部署相关/k3s/README.md",
    "k3s/calico": "docs/部署相关/k3s/3.Calico安装与网络验证.md",
    "k3s/ha": "docs/部署相关/k3s/2.安装部署-HA集群.md",
    "k3s/install": "docs/部署相关/k3s/1.安装部署-单节点.md",
    "k3s/servicelb": "docs/部署相关/k3s/5.什么是ServiceLB.md",
    "k3s/vs-rke2": "docs/部署相关/k3s/4.与RKE2对比.md",
    "k8s": "docs/部署相关/kubernetes/README.md",
    "k8s/cert-manager": "docs/部署相关/kubernetes/plugins/cert-manager/README.md",
    "k8s/cluster": "docs/部署相关/kubernetes/2.安装部署-集群.md",
    "k8s/cni": "docs/部署相关/kubernetes/3.网络模型与CNI选型.md",
    "k8s/cni-internals": "docs/部署相关/kubernetes/10.CNI插件原理.md",
    "k8s/deploy": "docs/部署相关/kubernetes/6.应用部署实战.md",
    "k8s/dr": "docs/部署相关/kubernetes/7.容灾实测.md",
    "k8s/ingress": "docs/部署相关/kubernetes/4.入口与暴露.md",
    "k8s/install": "docs/部署相关/kubernetes/1.安装部署-单节点.md",
    "k8s/loki": "docs/部署相关/kubernetes/plugins/loki/README.md",
    "k8s/longhorn": "docs/部署相关/kubernetes/plugins/longhorn/README.md",
    "k8s/metallb": "docs/部署相关/kubernetes/plugins/metallb/README.md",
    "k8s/metrics-server": "docs/部署相关/kubernetes/plugins/metrics-server/README.md",
    "k8s/monitoring": "docs/部署相关/kubernetes/9.监控与告警.md",
    "k8s/nfs": "docs/部署相关/kubernetes/plugins/nfs/README.md",
    "k8s/openebs": "docs/部署相关/kubernetes/plugins/openebs/README.md",
    "k8s/ops": "docs/部署相关/kubernetes/8.日常运维与排障.md",
    "k8s/rancher": "docs/部署相关/kubernetes/11.管理面板-Rancher.md",
    "k8s/storage": "docs/部署相关/kubernetes/5.存储方案.md",
    "kafdrop": "docs/部署相关/kafdrop/README.md",
    "kafka": "docs/部署相关/kafka/README.md",
    "kafka/baseline": "docs/部署相关/kafka/3.参数基线与配置模板.md",
    "kafka/dr": "docs/部署相关/kafka/5.容灾实测.md",
    "kafka/install": "docs/部署相关/kafka/1.安装部署-单机.md",
    "kafka/kraft": "docs/部署相关/kafka/2.安装部署-KRaft集群.md",
    "kafka/monitoring": "docs/部署相关/kafka/7.监控与告警.md",
    "kafka/ops": "docs/部署相关/kafka/6.日常运维与排障.md",
    "kafka/topics": "docs/部署相关/kafka/4.数据面topic与收发.md",
    "linux": "docs/部署相关/linux/README.md",
    "linux/kernel-tuning": "docs/部署相关/linux/1.内核参数与百万连接.md",
    "llamacpp": "docs/部署相关/llamacpp/README.md",
    "mqtts": "docs/技术方案/自签MQTTS技术方案/自签MQTTS技术方案.md",
    "mqtts/client-csr": "docs/技术方案/自签MQTTS技术方案/4.客户端密钥与CSR生成说明及Java示例.md",
    "mqtts/device-cert": "docs/技术方案/自签MQTTS技术方案/5.设备证书签发说明与Java示例.md",
    "mqtts/intermediate-ca": "docs/技术方案/自签MQTTS技术方案/2.中间证书生成说明与Java示例.md",
    "mqtts/root-ca": "docs/技术方案/自签MQTTS技术方案/1.根证书生成说明与Java示例.md",
    "mqtts/server-cert": "docs/技术方案/自签MQTTS技术方案/3.LB服务端证书生成说明与Java示例.md",
    "mysql": "docs/部署相关/mysql/README.md",
    "mysql/backup": "docs/部署相关/mysql/4.备份与恢复.md",
    "mysql/baseline": "docs/部署相关/mysql/2.参数基线与配置模板.md",
    "mysql/install": "docs/部署相关/mysql/1.安装部署-单机docker.md",
    "mysql/mgr": "docs/部署相关/mysql/5.MGR集群.md",
    "mysql/monitoring": "docs/部署相关/mysql/9.监控与告警.md",
    "mysql/ops": "docs/部署相关/mysql/8.日常运维与排障.md",
    "mysql/router": "docs/部署相关/mysql/6.MySQLRouter.md",
    "mysql/security": "docs/部署相关/mysql/3.账号与安全.md",
    "mysql/xtrabackup": "docs/部署相关/mysql/7.xtrabackup备份深入.md",
    "ops/daily-backup": "docs/运维巡检/备份-组件机每日OSS备份.md",
    "ops/inspect-ac-20261016": "docs/运维巡检/reports/inspect-ac-20261016.md",
    "oss/dual-bucket": "docs/技术方案/对象存储/5.双桶隔离方案-公开桶与私有桶设计说明.md",
    "oss/index-etl": "docs/技术方案/对象存储/3.对象存储的索引和ETL监控.md",
    "oss/intro": "docs/技术方案/对象存储/1.优雅的使用对象存储.md",
    "oss/kibana": "docs/技术方案/对象存储/7.ES索引模板与Kibana语法.md",
    "oss/metadata-index": "docs/技术方案/对象存储/6.OSS元数据索引设计-Elasticsearch方案.md",
    "oss/path-policy": "docs/技术方案/对象存储/4.S3存储桶公私路径配置说明.md",
    "oss/presigned": "docs/技术方案/对象存储/2.Java集成S3并实现预签名上传.md",
    "pve": "docs/部署相关/pve/README.md",
    "pve/disk-bench": "docs/部署相关/pve/4.盘参数性能实测.md",
    "pve/lvm-cache": "docs/部署相关/pve/3.LVM缓存迁移与实测.md",
    "pve/storage": "docs/部署相关/pve/1.存储配置.md",
    "pve/storage-bench": "docs/部署相关/pve/2.存储性能实测.md",
    "redis": "docs/部署相关/redis/README.md",
    "redis/baseline": "docs/部署相关/redis/2.参数基线与配置模板.md",
    "redis/cluster": "docs/部署相关/redis/6.Cluster集群.md",
    "redis/install": "docs/部署相关/redis/1.安装部署-单机docker.md",
    "redis/monitoring": "docs/部署相关/redis/9.监控与告警.md",
    "redis/ops": "docs/部署相关/redis/8.日常运维与排障.md",
    "redis/persistence": "docs/部署相关/redis/4.持久化与备份恢复.md",
    "redis/security": "docs/部署相关/redis/3.账号与安全.md",
    "redis/sentinel": "docs/部署相关/redis/5.主从与哨兵.md",
    "skills/inspect": "docs/skills/巡检/README.md",
    "skills/inspect-ac": "docs/skills/巡检/inspect-ac/README.md",
    "skills/inspect-ac-api": "docs/skills/巡检/inspect-ac/references/api.md",
    "skills/inspect-ac-skill": "docs/skills/巡检/inspect-ac/SKILL.md",
    "skills/inspect-mw": "docs/skills/巡检/inspect-middleware/SKILL.md",
    "skills/inspect-mw-emqx": "docs/skills/巡检/inspect-middleware/references/emqx.md",
    "skills/inspect-mw-es": "docs/skills/巡检/inspect-middleware/references/elasticsearch.md",
    "skills/inspect-mw-filebeat": "docs/skills/巡检/inspect-middleware/references/filebeat.md",
    "skills/inspect-mw-jenkins": "docs/skills/巡检/inspect-middleware/references/jenkins.md",
    "skills/inspect-mw-kafka": "docs/skills/巡检/inspect-middleware/references/kafka.md",
    "skills/inspect-mw-mysql": "docs/skills/巡检/inspect-middleware/references/mysql.md",
    "skills/inspect-mw-nacos": "docs/skills/巡检/inspect-middleware/references/nacos.md",
    "skills/inspect-mw-nginx": "docs/skills/巡检/inspect-middleware/references/nginx.md",
    "skills/inspect-mw-redis": "docs/skills/巡检/inspect-middleware/references/redis.md",
    "skills/inspect-mw-zk": "docs/skills/巡检/inspect-middleware/references/zookeeper.md",
    "skills/inspect-server": "docs/skills/巡检/inspect-server/SKILL.md",
    "skills/inspect-server-fixes": "docs/skills/巡检/inspect-server/references/fixes.md",
    "skills/sub2api-admin": "docs/skills/sub2api-admin/SKILL.md",
    "skills/sub2api-admin-cli": "docs/skills/sub2api-admin/references/admin-cli.md",
    "sub2api": "docs/部署相关/sub2api/README.md",
    "sub2api/install": "docs/部署相关/sub2api/1.安装部署-docker-compose.md",
    "tailscale": "docs/部署相关/tailscale/README.md",
    "tailscale/migration": "docs/部署相关/tailscale/1.办公网到10.61组网迁移(WG转tailscale).md",
    "valkey": "docs/部署相关/valkey/README.md",
    "valkey/compat": "docs/部署相关/valkey/2.与Redis兼容性实测.md",
    "valkey/install": "docs/部署相关/valkey/1.安装部署-docker.md",
    "valkey/replication": "docs/部署相关/valkey/3.主从复制与迁移路径.md",
    "zk": "docs/部署相关/zookeeper/README.md",
    "zk/baseline": "docs/部署相关/zookeeper/3.参数基线与配置模板.md",
    "zk/cli": "docs/部署相关/zookeeper/4.数据与命令面.md",
    "zk/cluster": "docs/部署相关/zookeeper/2.安装部署-集群.md",
    "zk/dr": "docs/部署相关/zookeeper/5.容灾与监控.md",
    "zk/install": "docs/部署相关/zookeeper/1.安装部署-单机.md",
}


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

# 自动派生时忽略的 ASCII 词（太泛或无意义）
STOP_TOKENS = {"readme", "index", "doc", "docs", "md"}


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


def load_existing_aliases():
    """读取现有 aliases.json；不存在就当没有，坏了提示后按种子重新生成。"""
    try:
        with open("aliases.json", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"⚠️ aliases.json 解析失败（{e}），将重新生成")
        return {}
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
    return {}


def derive_alias(rel: str):
    """从文档路径派生短别名：取路径里的 ASCII 词，目录一个 + 文件名最多三个。

    例：docs/部署相关/mysql/5.MySQL备份与恢复详解：mysqldump、xtrabackup与PITR实战.md
        → mysql/mysqldump-xtrabackup
    """
    stem = rel
    if DOC_ROOT:
        prefix = DOC_ROOT.rstrip("/") + "/"
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
    stem = re.sub(r"\.md$", "", stem, flags=re.I)
    parts = stem.split("/")

    def toks(seg):
        out = []
        for t in re.findall(r"[A-Za-z0-9]+", seg.lower()):
            if t.isdigit() or t in STOP_TOKENS:
                continue
            if not out or out[-1] != t:
                out.append(t)
        return out

    dir_tokens = []
    for seg in parts[:-1]:
        for t in toks(seg):
            if t not in dir_tokens:
                dir_tokens.append(t)
    head = dir_tokens[-1] if dir_tokens else ""
    body = "-".join(toks(parts[-1])[:3])
    if head and body.startswith(head + "-"):
        body = body[len(head) + 1:]
    alias = f"{head}/{body}" if head and body else (body or head)
    if not alias:
        alias = "p-" + hashlib.md5(rel.encode("utf-8")).hexdigest()[:6]
    return alias


def build_aliases():
    """生成 / 更新 aliases.json：手改 > 种子 > 自动派生，全量重建。"""
    md_files = collect_md_files()
    aliases = {}

    # 1) 现有文件里的手改条目：目标还在就保留（先到先得，保持 alias 与目标一一对应）
    kept_manual, dropped = 0, []
    for a, t in load_existing_aliases().items():
        a = a.strip().strip("/")
        if not a:
            continue
        if t not in md_files:
            dropped.append(f"{a} → {t}")
        elif a in aliases or t in aliases.values():
            dropped.append(f"{a} → {t}")
        else:
            aliases[a] = t
            kept_manual += 1

    # 2) 内置种子：只补现有条目没覆盖的
    from_seed, dead_seed = 0, []
    for a, t in ALIAS_SEED.items():
        if t not in md_files:
            dead_seed.append(f"{a} → {t}")
        elif a not in aliases and t not in aliases.values():
            aliases[a] = t
            from_seed += 1

    # 3) 剩下的文档自动派生
    covered = set(aliases.values())
    auto = 0
    for t in sorted(md_files - covered):
        base = derive_alias(t)
        a, n = base, 2
        while a in aliases:
            a = f"{base}-{n}"
            n += 1
        aliases[a] = t
        auto += 1

    with open("aliases.json", "w", encoding="utf-8") as f:
        json.dump(dict(sorted(aliases.items())), f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"✅ 已生成 aliases.json：{len(aliases)} 个别名"
          f"（保留手改 {kept_manual}，来自种子 {from_seed}，自动派生 {auto}）")
    if dropped:
        print(f"⚠️ 丢弃 {len(dropped)} 个无效或重复的现有别名：")
        for line in dropped[:5]:
            print(f"   {line}")
        if len(dropped) > 5:
            print(f"   … 以及另外 {len(dropped) - 5} 个")
    if dead_seed:
        print(f"⚠️ 种子里有 {len(dead_seed)} 个别名目标不存在（本机没有这些文档，已跳过）：")
        for line in dead_seed[:5]:
            print(f"   {line}")
        if len(dead_seed) > 5:
            print(f"   … 以及另外 {len(dead_seed) - 5} 个")
    uncovered = md_files - set(aliases.values())
    if uncovered:
        print(f"⚠️ 有 {len(uncovered)} 个文档没有别名（理论上不应出现）：")
        for path in sorted(uncovered)[:5]:
            print(f"   {path}")


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
