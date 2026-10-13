# AI 模型中转（sub2api）

团队共用 AI 编程能力的统一入口：模型厂商账号集中托管在 sub2api 网关后面，平台签发自己的 `sk-` Key 给同事，认证、调度、额度、计费收口在一处。部署与运维见 [1.安装部署-docker-compose](1.安装部署-docker-compose.md)。

- **版本基线**：sub2api 0.2.x（Docker Hub `weishaw/sub2api`，上游 GitHub Wei-Shaw/sub2api，LGPL v3），实测部署 0.2.3，上游最新 0.2.15（2026-10-09）
- **线上入口**：`https://sub2api.gsjhdev.top`，nginx + 通配符证书，后端 8080，三容器（app + PostgreSQL 18 + Redis 8）本地目录持久化
- **管理面**：后台网页 + 管理 API（Admin Key / JWT），批量账号操作可脚本化

## 文章索引

| 编号 | 文章 | 内容 |
| --- | --- | --- |
| 1 | [安装部署-docker-compose](1.安装部署-docker-compose.md) | 三容器形态、.env 必改项、首登、nginx 反代、升级迁移、已知坑 |

## 为什么是 sub2api

new-api 笔者部署过，就是有很多 bug——渠道配置、额度计算、转发链路上反复踩坑，更新频率也跟不上，用起来一直将就。sub2api 部署简单（官方 compose 起三个容器即完）、管理界面清楚，功能上正好对准"订阅额度分发"这个场景：多 OAuth 账号池、粘性会话、token 级计费、复合分组路由，是目前比较省心的选择。同类里 newapi/one-api 系是"功能堆得快、重构跟不上"的路子，fork 众多但主线质量参差，不作为底座。

## 模型分档与使用口径

先说结论：**重度任务用智谱 GLM 月费套餐，轻度任务用 DeepSeek-V4.1-Flash 或 MiMo-v2.6，前端编程用 Kimi-K3；Claude 与 Codex 不进中转，有需要的同事自行开通走报销。**

| 档位 | 模型 | 走什么 | 理由 |
| --- | --- | --- | --- |
| 重度任务 | 智谱 GLM 编程套餐 | 中转站 | 编程能力与 OpenAI/Claude 基本无差，价格与 Codex 套餐相当，官方 API 稳定、按量计费，适合集中托管 |
| 轻度任务 | DeepSeek-V4.1-Flash、MiMo-v2.6-flash/pro | 中转站 | 便宜且能打，覆盖大部分日常请求；DeepSeek V4-Pro 已下线，现为 V4.1-Flash（早期"V4-Pro 不如预期、V4-Flash 性价比高"的对比口径随之作废），MiMo v2.6 flash/pro 同档位补充，实测好用价格低 |
| 前端编程 | Kimi-K3 | 中转站 | 前端方向表现最好 |
| 高能力需求 | Claude、OpenAI Codex | **自行开通 + 报销** | 见下 |

Claude 和 Codex 模型能力确实强，但都不适合大范围开通、也不适合挂进中转站，原因不在能力在风险形态：

- **Claude**：国内使用封号风险太高，账号难以长期稳定持有；官方 API 又贵。封号损失不能由全员共同承担——挂到中转站上一封就是全员断供。
- **Codex**：能力较强，但最近一段时间走中转站的方式使用存在降智、限流、掺屎、报错等情况——降级问题出在中转链路（会话共享、额度摊薄），不代表直连不行，所以处理同样是收窄为直连。

两者的共同结论：**风险归个人，不归团队公共资产**。有需要的同事自行开通官方账号，费用走报销（额度上限与申请口径按团队规定执行），封号/降级只影响一个人，损失可控；中转站只承载官方 API 稳定、无封号焦虑的模型。Kimi 企业版（Kimi Business，¥4,200/座/年，2 座起售）是数据敏感小团队的备选，按人按年计费，人多总成本不低，是否选它取决于对数据安全的优先级。

## 客户端接入

拿到中转站域名和 API Key 后按习惯选接入方式：

- **官方客户端**：Claude、Codex 官方客户端通过 ccswitch 配置中转站域名与 API Key；
- **第三方客户端**：codewhale、opencode、reasonix 等填同一套地址与 Key；
- **国产客户端**：智谱 zcode、DeepSeek harness，配套自家模型；
- **IDE 插件**：IDEA、VS Code 的 AI 编程插件（如 CC GUI），配中转站与 Key 即用。

统一地址：`https://sub2api.gsjhdev.top/v1`（OpenAI 兼容，Key 为平台签发的 `sk-` 前缀）。
