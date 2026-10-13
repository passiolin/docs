# FCM 推送接入

App 推送统一走 Firebase Cloud Messaging（FCM）：业务事件写 Kafka，消费端批量查 token、渲染模板，经 Admin SDK `sendEach` 批量投递，Android/iOS/Web 共用一条链路。坑最密的一处在 iOS——.p8 密钥在 Apple 侧通吃 sandbox/production 两个环境，Firebase 控制台却要求开发版、正式版两个槽位各传一次，只传开发版时 TestFlight/正式签名设备全部投递失败。

## 文章索引

| 编号 | 文章 | 内容 |
|------|------|------|
| 1 | [FCM消息推送技术方案](1.FCM消息推送技术方案.md) | 接入方式对比（Admin SDK vs HTTP v1）、Kafka 批量消费与模板渲染、token 上报与生命周期、错误分类与重试、Topic/条件推送、监控与接入清单 |
| 2 | [Firebase云端配置指南](2.Firebase云端配置指南.md) | 从 0 开始：Google/Apple 账号、Firebase 项目与应用注册、APNs .p8 双槽位、服务账号密钥红线、三层验证与排障决策树、事故记录 |
