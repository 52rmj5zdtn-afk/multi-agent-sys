# PLC 数据链交接文档（真实验证版）

日期：2026-11-16
目标：无真 PLC 环境下，完成 从 SICAR PLC 数据 → OPC UA → MQTT → 时序库 的整条链并验证。

## 最终结论：链路已打通并验证通过

```
SICAR 程序 (TIA V20, PLCSIM Advanced 仿真 1516TF)
  → OPC UA 服务器 opc.tcp://192.168.1.6:4840 (ns=3 符号节点)
  → OpcuaSource (asyncua) → Collector → MqttPublisher (amqtt, 127.0.0.1)
  → TelemetryConsumer → DbSink → DeviceTelemetry 表
```

验证：`pytest tests/integration/test_real_plc_chain.py -v` → **1 passed**
（8 个采集周期全部发布成功，落库统计非空。）

## 关键事实（来之不易，后续别再踩坑）

1. **TIA 项目**：`F:\Sicar程序理解\巢湖三拉环线\Line03MCPBoZhonSiemens20260401\Line03MCPBoZhonSiemens20260401.ap20`
   主 PLC：Zone1_PLC1516TF，CPU 1516TF-3 PN，订货号 **6ES7516-3UP10-0AB0**（FW V4.0，2025 年新目录），F-CPU，安全密码 **`safety`**。

2. **PLCSIM Advanced 实例**：`Line03PLC`，X1=192.168.1.6/24（虚拟网卡“以太网 2”=192.168.1.100）。
   管理 API：`C:\Program Files (x86)\Common Files\Siemens\PLCSIMADV\API\7.0\Siemens.Simatic.Simulation.Runtime.Api.x64.dll`
   （PowerShell 可用；脚本在 `C:\tmp_kimi\plc_*.ps1`）。

3. **下载为什么只能走 TIA 仿真按钮**（坑 #1/#2/#3）：
   - Openness `DownloadProvider.Download()` 在本机一直报"连接到模块失败"——未解，但无关紧要。
   - 手动"扩展下载到设备"报 **0050 2 2 2458（硬件配置不兼容）**：项目 CPU 是 2025 年新订货号 3UP10，
     PLCSIM Advanced **V7.0.0.0 原版**（2024-12）的硬件配置加载器不认识它。
   - MRES/重建实例都不能解决（已逐一排除：实例类型、双仿真器冲突、脏存储、防火墙、系统时间、证书）。
   - **解法：TIA 菜单 在线(O) → 仿真(T)** —— TIA V20 内置的统一仿真器（与 PLCSIM Advanced 同一
     Runtime 后端）自动建实例并完成下载。这是本项目唯一验证可行的下载路径。
     （根治方案是给 PLCSIM Advanced 打 V7.0 Update 1，2025-09 发布，文档号 109988436，
      需西门子账号；或把项目 CPU 换成旧订货号 3UN00，脚本 `scripts/tia_change_cpu.py` 已备好。）

4. **下载后状态**：CPU 在 STOP（程序需要真实生产信号才转 RUN，不影响 OPC UA 数据链；
   OPC UA 服务器随固件运行，与 RUN/STOP 无关）。

5. **OPC UA 节点规则（实测确认）**：
   - NodeId 格式：`ns=3;s="DB名"."结构"[数组下标]."成员"` —— **数组下标用方括号且无引号**。
   - 可读性按 DB 的“可从 OPC UA 访问”属性逐个决定：
     可读：`GLOBAL_LASERWELDING_HMI_IF_DB`、`OPMODE07_DB`、`PRODUCTION_DATA_DB`、`HEART_DB`、I/Q/M 区；
     不可读（BadNotReadable）：`OPMODE07_EM040_DB`、`SYSTEM_DB`、`GENERAL_DB` 等——选点前必须 browse 验证。
   - 浏览导出：`deployment/opcua_nodes_browse.json`（全量 3657 节点）、
     `deployment/opcua_weld_subtrees.json`（焊接子树 8519 节点 8478 可读）。

6. **标签契约**：`services/edge_collector/tags_weld.yaml`（已按真实路径冻结）
   - cycle：`"GLOBAL_LASERWELDING_HMI_IF_DB"."Laser_Safety_New"[1]."WeldTotalNumTimes"`（实测值 24，单调）
   - 功率设定/实际：`...LaserTRUMPF[1].Laser.Output.LASER_POWER` / `...Laser.ActualLaserPower`
   - 出光、单点焊时、保护镜寿命、报警灯等同上表。
   - 程序里并列 3 套工位（下标 0/1/2），扩设备复制 devices 条目即可。

## 复现/运维命令

```bash
# 1. 确认仿真 PLC 在线（PLCSIM Advanced 实例 Line03PLC 需已启动；TIA 仿真按钮可重新装载）
python -c "import snap7; c=snap7.client.Client(); c.connect('192.168.1.6',0,1); print(c.get_cpu_state())"

# 2. 浏览地址空间（随时可重新导出）
python scripts/browse_opcua.py opc.tcp://192.168.1.6:4840
python scripts/browse_weld_subtrees.py opc.tcp://192.168.1.6:4840

# 3. 全链路集成测试（起 amqtt broker + 消费落库，约 7 秒）
python -m pytest tests/integration/test_real_plc_chain.py -v

# 4. 持续采集（生产形态）
python -m services.edge_collector.app --tags services/edge_collector/tags_weld.yaml
```

## 环境备注

- conda 环境 `dl`（D:/Tools/miniconda3/envs/dl），torch 已装。
- Windows 时间服务（W32Time）原本是停的，已启动并同步（证书校验敏感）。
- 防火墙放行 PLCENG TCP/UDP 102、34964、161 及 OPC UA 4840。
- S7-PLCSIM V20（经典版，随 TIA 的那个）不能与 PLCSIM Advanced 同时占用同一实例/IP，
  冲突时表现为下载报错目标变成 "CPUcommon"。
