# 集客 AC 无线网巡检(inspect-ac)

> - **设备**:集客 AC 管理口 `http://192.168.0.1:8080`,下挂 3 台 N3000 AP(固件 7.6_2024032600),模板 `gsjh`(id `default`),日常在线终端 50–60。
> - **巡检口径**:在线 AP 终端数 ≤ 3 视为无线服务异常,按其当前模板重新下发一次配置让 AP 重载无线配置,不整机重启。
> - **自包含**:巡检项定义、接口逆向记录、巡检脚本、处置手册全在本目录,可整体 symlink 为 ZCode skill 直接调用。

## 文件清单

| 文件 | 内容 |
| --- | --- |
| [SKILL.md](SKILL.md) | 巡检项定义(AC01–AC05)、阈值与分级、命令明细、处置手册(授权红线)——巡检体系的唯一事实源 |
| [ac-inspect.sh](ac-inspect.sh) | 巡检脚本:默认只读(退出码 0 健康 / 1 有 P0·P1 / 2 自身故障),`--fix` 为授权后的重推处置入口 |
| [references/api.md](references/api.md) | 管理 API 逆向记录(登录链路、apsearch/apcmd/overview 字段表)、脚本用法、实测记录与已知坑 |

## 快速开始

```bash
# 只读巡检
AC_PASS='管理密码' ./ac-inspect.sh

# 处置:对终端数异常的 AP 按模板重推配置(授权红线:运维负责人明确指示后运行)
AC_PASS='管理密码' ./ac-inspect.sh --fix
```

在 ZCode 中启用(供 AI 按 skill 巡检,见[巡检总纲](../README.md)):

```bash
ln -s "$PWD/docs/skills/巡检/inspect-ac" ~/.zcode/skills/inspect-ac
```
