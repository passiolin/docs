# OpenVPN(openvpn)

> - **版本基线**:`kylemanna/openvpn:latest`,镜像内 OpenVPN **2.4.9** + OpenSSL 1.1.1g(2020-04-20 构建);宿主 Ubuntu 26.04 LTS,Docker。
> - **部署环境**:两套独立部署——① 海外 VPC 的 `vpn-server`(10.0.0.11),EIP 203.0.113.10,域名 `vpn.example.com`,TCP 8443,2026-11-25;② 站点互联的 `vpn-hub`(10.0.0.21),TCP 8443,host 网络容器,2026-11-27。
> - **用途**:① 单客户端 `client1` 全隧道接入,借海外出口 IP 出网;② 多客户端站点互联,靠 ccd/iroute 打通各站点后端子网。
> - **隧道**:① 10.8.0.0/24(net30),服务端 10.8.0.1,容器内 MASQUERADE;② 10.9.0.0/24(net30),服务端 10.9.0.1,不做 NAT。
> - 文中的域名、IP、客户端名均为示例值,部署时替换为实际值。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [OpenVPN服务端Docker部署与出口IP](1.OpenVPN服务端Docker部署与出口IP.md) | kylemanna 镜像、genconfig 参数、PKI、容器运行、客户端签发、出口 IP 验证、坑表 | ✅ 已实测 |
| 2 | [OpenVPN站点互联与子网路由](2.OpenVPN站点互联与子网路由.md) | 服务端 route 与客户端 ccd/iroute 的配合、后端子网打通步骤、重启生效、验证与坑表 | ✅ 已实测 |

## 阅读路径

- 从零部署:第 1 篇「服务端部署」→「客户端签发与使用」→「验证」;
- 只关心出口 IP:第 1 篇「部署形态」与「验证」;
- 站点互联、给客户端加后端子网:第 2 篇「route 与 iroute 的分工」→「添加客户端后端子网的操作」→「坑表」;
- 排障:第 1 篇「坑表」、第 2 篇「坑表」。

## 组件说明

OpenVPN 基于 TLS,服务端与客户端各持证书,通过 tun 设备建立点对点隧道。kylemanna/openvpn 把 easy-rsa 与 OpenVPN 打进镜像:`ovpn_genconfig` 生成服务端配置,`ovpn_initpki` 建 PKI,`ovpn_getclient` 导出带内联证书的客户端配置。本目录记录两套部署:海外跳板机上的 Docker 全隧道出口,以及站点互联场景下用 ccd/iroute 打通多个后端子网。
