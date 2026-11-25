# OpenVPN 服务端 Docker 部署(openvpn)

> - **版本基线**:`kylemanna/openvpn:latest`,镜像内 OpenVPN **2.4.9** + OpenSSL 1.1.1g(2020-04-20 构建);宿主 Ubuntu 26.04 LTS,Docker。
> - **部署环境**:海外 VPC 的 `vpn-server`(10.0.0.11),EIP 203.0.113.10,域名 `vpn.example.com`,TCP 8443,2026-11-25。
> - **用途**:单客户端 `client1` 全隧道接入,借海外出口 IP 出网;不做 ccd/iroute。
> - **隧道**:10.8.0.0/24(net30),服务端 10.8.0.1,容器内 MASQUERADE。
> - 文中的域名、IP、客户端名均为示例值,部署时替换为实际值。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [OpenVPN服务端Docker部署与出口IP](1.OpenVPN服务端Docker部署与出口IP.md) | kylemanna 镜像、genconfig 参数、PKI、容器运行、客户端签发、出口 IP 验证、坑表 | ✅ 已实测 |

## 阅读路径

- 从零部署:第 1 篇「服务端部署」→「客户端签发与使用」→「验证」;
- 只关心出口 IP:第 1 篇「部署形态」与「验证」;
- 排障:第 1 篇「坑表」。

## 组件说明

OpenVPN 基于 TLS,服务端与客户端各持证书,通过 tun 设备建立点对点隧道。kylemanna/openvpn 把 easy-rsa 与 OpenVPN 打进镜像:`ovpn_genconfig` 生成服务端配置,`ovpn_initpki` 建 PKI,`ovpn_getclient` 导出带内联证书的客户端配置,适合单机快速部署。本目录记录的是海外跳板机上的 Docker 部署与出口 IP 验证。
