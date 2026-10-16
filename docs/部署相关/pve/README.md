# PVE 宿主机存储方案(10.10.10.44)

> - **机器**:PVE 9.2.2(kernel 7.0.2-6-pve,OpenZFS 2.4.2-pve1),Xeon E5-2698B v3(32 线程),251G 内存。
> - **负载画像**:39 个虚机——iKUAI 路由、2080Ti 直通工作站(131G 内存)、开发/远程机,以及 26 台停机待批的数据库 lab(mysql/kafka/doris/es/zk 全家桶)。
> - **盘位现状(2026-10-10 盘点)**:nvme0 512G 系统盘(root + local-lvm,**90% 满**);nvme1 960G SK hynix = `/nvme` ext4,5 个活跃 VM 共 537G;sda 500G WD5000AAKX = `/hdd` ext4,vzdump 备份 + ISO + iKUAI 盘。
> - **方案一句话**:4TB HGST 7K4000(二手)建单盘 ZFS 池收全部数据正本,nvme1 切 SLOG 128G + L2ARC 剩余(~766GiB)做加速件,旧 500G 收敛为纯备份盘;VM 盘走 **raw-on-dataset + aio=threads + sync=standard**(qcow2 与 io_uring 在此池上有实测级性能罚),zstd 压缩照吃,克隆日常走 PVE 原生。性能天花板先说清:加速件只兑现 fsync 确认(80µs)与热读命中,持续吞吐就是磁头本身(~100-130MB/s 顺序、百级 IOPS 4K 随机),要 NVMe 速度的 VM 盘必须物理落 NVMe(如 114 在 local-lvm:读 2213 / 写 822MB/s)。
> - **现态(2026-10-16 迁移后)**:4T 迁为 LVM(hdd VG:ext4,PVE dir 存储 hddvm),960G NVMe 全量转作该卷的 dm-cache **writeback**(880G/64K/smq),ZFS 退役;迁移六阶段、验收实测与回退键见 [3.LVM缓存迁移与实测](3.LVM缓存迁移与实测.md)。上一条 ZFS 拓扑自此为历史方案。

## 文章索引

| 编号 | 文章 | 内容 |
| --- | --- | --- |
| 1 | [存储配置](1.存储配置.md) | 建池、数据集、SLOG/L2ARC 分区、storage.cfg、克隆与快照操作、巡检命令 |
| 2 | [存储性能实测](2.存储性能实测.md) | tank(HDD+缓存)与 local-lvm(NVMe)的 fio 方法论、调优时间线、实测矩阵与踩坑清单 |
| 3 | [LVM缓存迁移与实测](3.LVM缓存迁移与实测.md) | ZFS→LVM+dm-cache 迁移六阶段、踩坑、热数据 fio 五件套与 fsync 特性、回退键与巡检 |

## 一、要解决的问题

三个存量问题拼出这次重排:

1. **local-lvm 90% 满**:26 台 lab VM 挤在 348G thinpool 里,系统盘余量告急;
2. **备份位吃紧**:`/hdd` 已用 59%(244G/458G),且 `prune-backups keep-all=1` 从不清理,102 单份备份就 260G;
3. **存储能力空白**:ext4 + qcow2 无压缩、无快照体系、无秒级克隆,全部 VM 盘性能压在 nvme0 一块系统盘上。

## 二、目标拓扑

| 盘 | 角色 | 内容 |
| --- | --- | --- |
| nvme0 512G(不动) | 系统盘 | PVE + local-lvm,角色收缩为系统与临时 |
| **4TB HGST 7K4000(新,二手)** | **tank:数据正本池** | VM 盘(raw)、ISO/模板、冷数据 |
| nvme1 894GiB SK hynix | tank 加速件 | SLOG 分区 128G + L2ARC 分区剩余 ~766GiB |
| sda 500G WD5000AAKX | hdd:专用备份 | vzdump 备份(现有 260G 原地保留) |

分工逻辑:正本落机械盘,热点落 NVMe,备份落最老的盘——备份是最不挑速度的负载,旧盘转岗继续用。底线:VM 正本与备份永远不同物理盘,单盘故障不会同时失去数据与其副本。

## 三、备份(落点待定)

- 旧 500G 已随换盘离场,`/hdd` 存储定义与根分区残留目录待清理;现有备份(102 的 260G)已不在机内;
- 备份落点是当前唯一待决策项:tank 上建 backup 数据集先顶着(防误删,不防盘坏)/ 增购备份盘 / 备去局域网其他机器;
- 落点定下后配 prune:keep-daily=7, keep-weekly=4, keep-monthly=3。

## 四、执行顺序(每步独立可回退)

1. 4TB 全盘读测(~6 小时),建池 + 数据集 + 存储定义(纯增量,零风险);
2. 迁海力士上 5 个 VM(537G)入 tank:`qm move-disk` 在线迁移,102 的 750G 盘约 1 小时;迁完清 `vgnvme` VG、fstab、nvme 存储定义;
3. 海力士切分区,`zpool add` 挂 SLOG + L2ARC(命令见 [1.存储配置](1.存储配置.md) 第三节);
4. 删 fnOS 测试机 VM 105,`pvesm remove hdd`,清根分区残留 `/hdd` 目录;
5. 克隆日常走 PVE 原生,golden 与 reflink 备用方案按需再建。

**存量不迁**:local-lvm(系统盘原生 NVMe)上的 iKUAI 与 26 台 lab VM 留在原地——随机 I/O 上原生 NVMe 优于 HDD+缓存,迁移只亏不赚。tank 定位是增量出厂与容量层:新 VM 默认 tankvm,local-lvm 只减不增。分区命令中的 nvme 枚举名已漂移(海力士现为 nvme0、系统盘为 nvme1),一律按 [1.存储配置](1.存储配置.md) 的 by-id 写法执行。

## 五、新盘入列体检(二手 HGST 7K4000)

HUS724040ALE641 = HGST Ultrastar 7K4000(7200 转企业盘;二手市场因西数收购 HGST 常误标为"西数紫盘")。Ultrastar 全系 CMR,无 SMR 之虞,vzdump 顺序写不受影响。到货先体检再入池:

```bash
smartctl -A /dev/sdX
#   红线:5 Reallocated=0、197 Current_Pending=0、198 Offline_Uncorrectable=0、199 UDMA_CRC≈0
#        任何一项非零即退换;9 Power_On_Hours 几万小时属 survivor 盘正常水平
smartctl -t long /dev/sdX                          # 长测,4TB 约 6-8 小时
dd if=/dev/sdX of=/dev/null bs=1M status=progress  # 或全盘只读扫一遍,~6 小时
```

## 六、被否路线备查

| 路线 | 否因 |
| --- | --- |
| NAS 虚机(fnOS)+ NFS 回供宿主机 | 回环税落在 DB fsync 上;NAS VM 故障 = 全厂 IO 冻结;PVE 侧快照/克隆退化 qcow2 语义 |
| special vdev | 落位策略不是缓存;容量耦合,单盘池上再叠一个 SPOF |
| bcache | 缓存盘挂掉后 backing 盘无法启动 |
| dm-cache / bcache writeback(裸回写) | 缓存盘扣押脏块唯一副本,挂 = 文件系统级损坏 ——2026-10-16 复评:测试机且数据可重建的前提下已采纳 dm-cache writeback(见 [3.LVM缓存迁移与实测](3.LVM缓存迁移与实测.md));bcache 的"缓存盘挂 backing 盘起不来"结论不变 |
| 链接克隆(qcow2 backing / zfs clone) | 不作默认:克隆链依赖与"完整克隆"预期不符;着急时可临时用 |

## 七、待确认

- 机箱空余 SATA 口、供电与盘位(C610 控制器还有 5 个口,物理位待查);
- 新盘到货即做第六节体检;
- VM 105(fnOS 测试机)待删:测试用 ZFS/Btrfs 存储空间与缓存配置都在其虚拟盘上,无数据。
