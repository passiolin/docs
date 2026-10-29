# OpenWrt 分流(openwrt)

> - **设备与版本**:办公网关 x86 OpenWrt 24.10.5;家用移动 AX3000(MT7981,kwrt 25.12-SNAPSHOT)。两台都是 PBR 1.2.2 + firewall4(nftables),分流判定同源。
> - **数据时间**:双 WAN 篇的计数器与 traceroute 抓于 2026-10-27;家用篇的隧道与 DNS 链路在 2026-10-28 落地,客户端实测于 2026-10-29 凌晨。文中命令与输出均取自这两台机器。
> - **出口形态**:办公网关是双 WAN,wan 进上游 VPN、wan2 走国内线路;家用是单 WAN 加一条到香港的 OpenVPN 隧道,pppoe-wan 跑国内、其余进隧道。

## 文章索引

| 编号 | 文章 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | [OpenWrt 双 WAN 按目标 IPv4 分流](1.OpenWrt双WAN按目标IPv4分流.md) | 6525 条 CIDR 灌 nft 集合、四条策略的链序、`pbr_output` 补规则、集合持久化与地址库日常更新 | ✅ 已实测 |
| 2 | [家用 OP 路由器外网访问并分流](2.家用OP路由器外网访问并分流.md) | OpenVPN 客户端接入、fw4 zone 漏配导致全断、smartdns 分国内/国外两组上游、隧道看门狗 | ✅ 已实测 |

## 阅读路径

- **先在办公网做双出口**:1(策略、集合、持久化)→ 2 的第五节(同一套 PBR 换到单 WAN + 隧道);
- **只关心 DNS**:2 的第六节,三次失败的尝试和最终的 domain-set 分组;
- **排障**:1 的第十节,接口、路由表、集合、计数器四层验证。

## 两台的差别

判定都落在"目标 IP 属于国内集合"上,差别在默认出口和 DNS:

- 办公网关:默认 wan 进上游 VPN,wan2 只跑国内;DNS 只有一个上游 `192.168.10.1`,走 UDP。
- 家用:默认出口是隧道铺的 `/1` 路由,国内集合命中才回 pppoe-wan。家用要抗污染,国外域名必须交给隧道里的上游,所以 DNS 分成 domestic / foreign 两组,再加一张国内域名表。

## 参考资料

- PBR 文档:<https://docs.openwrt.melmac.ca/pbr/1.2.2/>
- PBR 源码:<https://github.com/openwrt/packages/tree/master/net/pbr>
- 国内 IPv4 列表:<https://gaoyifan.github.io/china-operator-ip/china.txt>
- 国内域名表:<https://github.com/felixonmars/dnsmasq-china-list>
- smartdns:<https://github.com/pymumu/smartdns>
- kylemanna/openvpn:<https://github.com/kylemanna/docker-openvpn>
