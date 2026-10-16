# 集客 AC 接口与脚本(inspect-ac / references/api)

> - **设备**:集客 AC(管理口 `http://192.168.0.1:8080`),下挂 3 台 N3000 AP(固件 7.6_2024032600),模板 `gsjh`(模板 id `default`)。
> - **巡检口径**:在线 AP 的终端数(`clientnum`)≤ 3 视为无线服务异常,处置动作是按其当前模板重新下发一次配置,不动整机重启。
> - **实现**:[ac-inspect.sh](../ac-inspect.sh) 默认只读巡检(退出码 0 健康 / 1 有异常 / 2 巡检自身故障),`--fix` 为处置入口;巡检项定义与处置手册的权威版本在 [inspect-ac skill](../SKILL.md)。

## 一、接口链路(逆向自控制台前端)

控制台是 SPA,接口定义可以从登录后的前端 bundle(`/assets/index-*.js`)里 grep 出来;固件升级后如果接口对不上,重新抓一遍 bundle。三个接口串成一条链,均返回 `{ret, msg, ...}`,**`ret==1` 是唯一成功判据**,`msg` 常是 `LANGxxx` 码,界面靠内置字典翻译成中文。

### 1. 登录换 token

```bash
random=$(curl -s http://192.168.0.1:8080/api/getrandom | jq -r .random)   # {"random":"8926","ret":1}
hash=$(printf '%s' "${random}管理密码" | md5sum | awk '{print $1}')        # md5(随机数 + 明文密码)
curl -s -X POST http://192.168.0.1:8080/api/sysauth \
  -H 'Content-Type: application/json' -d "{\"password\":\"$hash\"}"
# {"msg":"","ret":1,"token":"ce42a9776a906ebf3b27e7e5cd813764"}
```

明文密码不进网络——服务端先发随机数,客户端算 `md5(random + password)` 只传哈希。密码错误返回 `{"ret":0,"msg":"LANG646","token":""}`(LANG646 = 账号或密码错误)。token 前端口径 30 分钟无操作过期,脚本每次现登,不依赖会话续期。

### 2. 拉全量 AP 列表

```bash
curl -s 'http://192.168.0.1:8080/api/ac/apsearch?pagenum=1&numperpage=500&groupid=all&_t=<毫秒时间戳>' \
  -H "sysauth: <token>"
```

鉴权走请求头 `sysauth`(不是 cookie,浏览器里存的是 localStorage)。`numperpage=500` 是控制台自己也在用的口径,一次拿全;超过 500 台才需要翻页。响应里巡检要用的字段:

| 字段 | 示例 | 说明 |
| --- | --- | --- |
| `name` | `ap-2` | 设备名 |
| `mac` | `a439b6199589` | **12 位无冒号小写**,不是界面显示的 `A4:39:...` 格式;下发接口也吃这个格式 |
| `ip` | `192.168.0.102` | 管理 IP |
| `clientnum` | `9` | 终端数,巡检判异字段 |
| `online` | `yes` / `no` | 在线状态 |
| `template` | `default` | 模板 **id**;界面显示的 `gsjh` 是模板名,下发接口传 id |
| `uptime` | `26329` | 在线时长(秒),判断"刚重启导致终端数为 0"时参考 |

### 3. 异常 AP 重新下发配置

```bash
curl -s -X POST http://192.168.0.1:8080/api/ac/apcmd \
  -H "sysauth: <token>" -H 'Content-Type: application/json' \
  -d '{"cmd":"settemplate","maclist":"a439b6199589","template":"default"}'
# {"msg":"LANG687","ret":1,"param":null}
```

`maclist` 是逗号拼接的 12 位 mac 串,多台一次推;`template` 用各 AP 自己的 `template` 字段(空则按 `default`)。等价于界面上「无线 AP」页勾选设备后点「重新下发配置」:同模板重推会保留各 AP 单独设置的信道/频宽(界面确认弹窗原文),AP 重载无线配置,不断电不重启。

界面那个按钮有个坑:**不勾选直接点,会把当前页全部 AP 都重推一遍**(确认弹窗原文也写明了)。所以巡检必须脚本按 mac 精确"勾选",别在界面上顺手点。

## 二、脚本用法

[ac-inspect.sh](../ac-inspect.sh) 依赖 `curl`、`jq`、`md5sum`,密码经环境变量传入、不落盘。默认只读,一轮跑完 5 个巡检项(AC01 服务可用、AC02 AP 在线率、AC03 终端数异常、AC04 版本一致性、AC05 终端总数对账),阈值与分级以 [inspect-ac skill](../SKILL.md) 为准:

```bash
# 只读巡检(报告异常项 + 豁免项,退出码 0/1/2)
AC_PASS='管理密码' ./ac-inspect.sh

# 处置:对 AC03 异常 AP 按各自模板重推配置——巡检体系授权红线,运维负责人明确指示后运行
AC_PASS='管理密码' ./ac-inspect.sh --fix

# 临时改判异阈值 / 刚重启豁免窗口(秒)
AC_PASS='管理密码' THRESHOLD=10 UPTIME_GRACE=300 ./ac-inspect.sh
```

输出顺序:**下发配置声明(有/无,固定为首行结论)** → 全量清单(名称/IP/连接数/状态/版本/模板)→ 异常项(级别|编号|巡检项|当前值|阈值|处置建议)→ 豁免项(刚重启的 AP)→ 仅 `--fix` 时追加处置下发记录。重推后终端逐个回流,**闭环以下一轮巡检的连接数回到基线为准**。

cron 示例(密码放 root-only 的 env 文件,避免进 crontab 明文;只读模式可直接挂,`--fix` 模式不建议无人值守):

```cron
# 每天早高峰前巡一遍,终端已经上来的时段数据才真实
37 8 * * *  set -a; . /root/.ac-inspect.env; set +a; /opt/ac-inspect.sh >> /var/log/ac-inspect.log 2>&1
```

## 三、实测记录(2026-10-16)

- 登录链路:`getrandom` → `md5(盐+密码)` → `sysauth`,curl 实测换到 token;错误密码实测返回 `LANG646`。
- `apsearch` 实测 3 台全回(35/11/15 台终端,全部在线,单版本),阈值 3 下无异常,只读巡检通过(退出码 0);`THRESHOLD=50` 触发 3 条 AC03 异常(退出码 1);错误密码退出码 2。
- `apcmd settemplate` 实推一次(ap-2,11 台终端)返回 `ret:1`;前端 bundle 中的确认文案与字段格式(逗号串 maclist、模板 id)与实测一致。
- `overview`(simplify=no)实测含 `acstatus.client_num`、`memfree/memtotal`、`cputable` 等字段;`/api/status/gstatus` 本固件返回 404(完整版实体 AC 才有),AC 在线判定走登录链路。
- `--fix` 处置分支在 mock AC 上端到端验证:AC02 离线 P1、AC03 命中与刚重启豁免、AC04 版本不一致 P2、AC05 对账偏差 P2 全部触发,且按模板分组推送(`maclist`/`template` 载荷逐条核对)。

## 四、已知坑

| 现象 | 原因与处置 |
| --- | --- |
| `msg` 全是 `LANG646`/`LANG687` 这类码 | 服务端返回 i18n key,以 `ret` 为判据;LANG646 = 密码错,LANG687 = 下发受理 |
| 刚重启的 AP 被判异常(`clientnum=0`) | 终端还没关联回来,重推一次无害;介意就在判异时对照 `uptime` 人工看一眼 |
| 离线 AP 出现在清单里 | 脚本不推离线设备(推不动),单独列出等人工上电 |
| mac 写成 `A4:39:B6:...` 格式导致下发无效 | 接口只认 12 位无冒号小写,界面展示格式是加过冒号的 |
| 固件升级后接口对不上 | 字段与路径逆向自前端 bundle,升级后重新 grep `/assets/index-*.js` 核对一遍 |
| 密码哈希只有 md5(盐+明文) 一层 | 算法由固件定死改不了,这套接口只在内网用,管理口别暴露到公网/跨网段 |
