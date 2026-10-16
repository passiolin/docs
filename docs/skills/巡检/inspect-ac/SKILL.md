---
name: inspect-ac
description: Use when 巡检无线网:集客 AC 与下挂 AP 的服务可用性、AP 在线率、AP 终端数、固件版本一致性、终端总数对账;也为编写无线巡检脚本、解读 AP 异常(AP 掉线、终端数掉零、无线假死)、给出处置建议提供权威的巡检项、接口调用链与阈值。用户提到"AC 巡检""无线巡检""AP 掉线""AP 终端数异常""重推 AP 配置"等均适用。无 exporter 指标面,数据来源为 AC 管理 HTTP API(补盲面),配套巡检脚本 ac-inspect.sh。
---

# 无线网巡检(inspect-ac)

## 定位与范围

- **覆盖对象**:集客 AC(`http://192.168.0.1:8080`,软 AC,2026-10-16 实测固件 7.6_2024032600)与下挂 AP(3×N3000,模板 `gsjh`/id `default`,日常在线终端 50–60);
- **不覆盖**:AC 宿主机与 AP 所在链路的 OS/网络层指标(归 inspect-server)、POE 供电与机房环境(归 inspect-datacenter)、终端侧业务问题(归 inspect-service);
- **数据来源只有接口面**:AC 无 exporter,管理 HTTP API 即补盲脚本;接口链路的完整逆向记录(getrandom/md5 加盐登录、apsearch、apcmd、overview 字段表、LANG 码)是唯一事实源,见 [references/api.md](references/api.md)。

## 依赖与前置条件

执行巡检前确认,不满足时在报告中显式标注"数据缺失",不允许静默跳过:

1. AC 管理口从巡检机可达:`curl -fsS http://192.168.0.1:8080/api/getrandom` 返回 `ret:1`;
2. 管理密码经环境变量 `AC_PASS` 传入,不落盘;登录链路为 `md5(服务端随机数 + 密码)` 换 token,请求头 `sysauth` 携带,token 30 分钟闲置过期——每轮巡检现登,不缓存;
3. 脚本依赖 `curl`、`jq`、`md5sum`;
4. 管理口为内网服务且密码哈希仅 md5(盐+明文) 一层,**不得暴露到公网/跨网段**。

## 使用方式

- **AI 直接执行巡检**:按"巡检项清单"逐项调接口判定,按"报告要求"产出 Markdown 报告;一条登录链路可支撑全部巡检项,先登录取 token 再逐项查;
- **AI 编写/维护巡检脚本**:巡检项、接口、阈值、分级**一律以本文件为准**,不得自行发明;新增项先补本文件(编号顺延 AC 序列)再同步 [ac-inspect.sh](ac-inspect.sh);
- **处置纪律(授权红线)**:巡检(包括 AI)只产出报告与处置建议;重推配置(AC03)、重启 AP、重电 AP 都是处置动作,**必须在运维负责人明确指示后才启动**。脚本默认只读,`--fix` 参数即"负责人已授权本轮处置"的显式入口,不含 `--fix` 的运行永远不会下发任何命令;
- **豁免约定**:刚重启 AP 的终端数豁免(UPTIME_GRACE)由脚本自动标注,其余豁免须在报告中逐条写明对象与原因。

## 巡检项清单

阈值均为本环境基线(单 AC + 个位数 AP 的园区网),多 AC 或 AP 扩容后按比例重订,覆盖需注明原因。来源均为 AC 管理 API。

| 编号 | 巡检项 | 来源 | 默认阈值 P1 / P0 | 处置建议 |
| --- | --- | --- | --- | --- |
| AC01 | AC 服务可用(登录链路) | 接口 | — / 登录失败或接口不可达 | 查 AC 进程与宿主机,联动 inspect-server;AC 挂即全屋无线失控 |
| AC02 | AP 在线率 | 接口 | 任一离线 / 全部离线 | 单台查 POE 供电与网线、重电;全部离线先查 AC 与上联 |
| AC03 | AP 终端数异常 | 接口 | ≤3(在线 ≥10min)/ 0(非重启) | 按各自模板重推配置(须授权);复发转重启 AP 或查硬件 |
| AC04 | AP 固件版本一致性 | 接口 | —(P2)/ 低版本标记 | 低版本走升级流程,变更窗口执行 |
| AC05 | 终端总数对账 | 接口 | 差 > max(5, 20%)(P2)/ — | 两接口有取样时间差,持续偏差查假死终端 |

## 检查命令明细

以下命令均已在本环境实测(2026-10-16,curl+jq)。`$token` 通过登录链路取得,登录与鉴权细节见 [references/api.md](references/api.md),此处不再重复。

**AC01 服务可用**(getrandom → sysauth → apsearch 全链路任一步失败即 P0;密码错返回 `LANG646`)

```bash
random=$(curl -fsS "http://192.168.0.1:8080/api/getrandom" | jq -er '.random')
hash=$(printf '%s' "${random}管理密码" | md5sum | awk '{print $1}')
token=$(curl -fsS -X POST "http://192.168.0.1:8080/api/sysauth" \
  -H 'Content-Type: application/json' -d "{\"password\":\"$hash\"}" | jq -er '.token')
curl -fsS "http://192.168.0.1:8080/api/ac/apsearch?pagenum=1&numperpage=500&groupid=all" \
  -H "sysauth: $token" | jq -e '.ret == 1'
```

**AC02 + AC03 + AC04**(一次 apsearch 全覆盖;关键字段:`online` yes/no、`clientnum` 终端数、`uptime` 在线秒数、`version`、`mac` 12 位无冒号小写、`template` 模板 id)

```bash
curl -fsS "http://192.168.0.1:8080/api/ac/apsearch?pagenum=1&numperpage=500&groupid=all" \
  -H "sysauth: $token" | jq -r '.aplist[] | [.name,.ip,.clientnum,.online,.uptime,.version,.template] | @tsv'
# AC02:online != "yes" → P1
# AC03:online == "yes" 且 clientnum <= 3 且 uptime >= 600 → P1;uptime < 600 记豁免(刚重启,终端未回流)
# AC04:version 不唯一或 is_low_version == true → P2
```

**AC05 终端总数对账**(apsearch 之和 vs overview,持续偏差说明有假死终端或计数口径问题)

```bash
curl -fsS "http://192.168.0.1:8080/api/status/overview?simplify=no" -H "sysauth: $token" | jq '.acstatus.client_num'
# |Σ apsearch.clientnum − overview.acstatus.client_num| > max(5, 20%·AC 侧值) → P2
# 注:/api/status/gstatus 在本固件返回 404,AC 在线与否以登录链路 + overview 可达为准
```

## 处置手册(初步参考;处置须运维负责人明确指示,AI 不得自行执行)

### AC03 终端数异常 → 重新下发配置

**定位**:确认该 AP `uptime` 大于豁免窗口(排除刚重启);对照同组其他 AP 的终端数,单台掉零多为 AP 无线服务假死,多台同时掉零先查 AC 侧与干扰源。

**处置**(等价于界面「无线 AP」页勾选设备 → 「重新下发配置」):

```bash
curl -fsS -X POST "http://192.168.0.1:8080/api/ac/apcmd" \
  -H "sysauth: $token" -H 'Content-Type: application/json' \
  -d '{"cmd":"settemplate","maclist":"<12位mac,多台逗号拼接>","template":"<该AP的template字段>"}'
# ret==1 即受理成功(msg 是 LANG 码,LANG687 = 受理);多模板分组逐组推送
```

**验证**:受理后终端逐个回流,以**下一轮巡检的 clientnum 回到基线**为准,不是接口返回。

**风险**:AP 重载无线配置,本 AP 上终端瞬断数秒;同模板重推保留各 AP 单独设置的信道/频宽(界面确认弹窗口径)。反复命中 AC03 的 AP 说明不是配置态问题,处置升级为重启 AP(断电重连),仍复发查硬件/送检。**界面按钮不勾选直接点会把当前页全部 AP 都重推**,界面手工操作时勾选后再点。

### AC02 AP 离线

**定位**:查 POE 交换机端口供电与网线(联动 inspect-datacenter);AP 灯效与上联口流量。

**处置**:重电 POE 口或冷启动 AP;仍不上线换线换口,再不行送检。AI 不执行任何物理动作与端口操作,产出定位结论与建议即可。

## 报告要求

- **输出固定先答两问**(AI 答复与脚本输出同口径,缺一不可):①**是否有下发配置**——只读巡检必须明写"无下发",`--fix` 处置必须列出推送对象、模板与受理结果;②**逐台 AP 连接数**——每台 AP 一行(名称/IP/连接数/在线状态),不聚合、不省略;
- 沿用体系约定:异常项置顶、按级别排序,逐条含**当前值、命中阈值、处置建议**;豁免项单独列表;命名 `inspect-ac-YYYYMMDD.md`;
- [ac-inspect.sh](ac-inspect.sh) 已实现全部巡检项的判定与退出码约定(0 健康 / 1 存在 P0·P1 / 2 巡检自身故障),输出为日志行,可重定向存档后按本节格式整理成报告;`--fix` 运行会在日志中逐条记录处置下发结果;
- cron 建议:每日早高峰前(终端已回流,数据才真实),如 `37 8 * * *`,密码放 root-only env 文件。

## 巡检项的新增与修改

1. 先修改本文件:清单表加行、明细补命令,阈值变动写明原因;
2. 再同步 [ac-inspect.sh](ac-inspect.sh) 与接口事实源文档([1.巡检流程.md](references/api.md));
3. 新增项编号顺延(AC06、AC07…),不复用已废弃编号;固件升级后接口字段有变的,先核对 [1.巡检流程.md](references/api.md) 的逆向记录再改判定逻辑。
