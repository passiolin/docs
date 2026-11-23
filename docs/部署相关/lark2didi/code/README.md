# lark2didi 代码与脚本

本目录是同步服务的代码与脚本副本，部署与运维说明见 [../1.飞书到滴滴组织架构同步部署与运维.md](../1.飞书到滴滴组织架构同步部署与运维.md)。与独立代码仓库同源，改动后两边同步。

| 路径 | 内容 |
| --- | --- |
| `lark2didi/` | 同步服务源码：飞书只读、滴滴读写、计划、离职、通知 |
| `scripts/hourly.sh` | cron 入口，依次跑新增、部门、离职 |
| `tests/` | 70 个单元测试 |
| `pyproject.toml`、`uv.lock` | 依赖与锁文件 |

配置模板在 [../conf/env.example](../conf/env.example)。在 `code/` 下创建 `conf/` 并复制模板：

```bash
mkdir -p conf
cp ../conf/env.example conf/.env
chmod 600 conf/.env
uv sync
uv run pytest
```

`scripts/hourly.sh` 里的部署路径是 `/opt/lark2didi`，换目录部署时改这一处。本目录不含真实 `.env`、`state/`、`logs/`。
