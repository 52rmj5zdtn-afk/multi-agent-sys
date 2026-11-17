# TIA Portal 侧 OPC UA 服务器配置手册（S7-1500，TIA V20）

> 前置说明：基础版 S7-PLCSIM **不支持** S7-1500 OPC UA 服务器仿真，
> OPC UA 联调需 **PLCSIM Advanced**（或真机）。本仓库已内置
> `services/edge_collector/opcua_server_sim.py`（S7-1500 风格 OPC UA 仿真
> 服务器，Basic256Sha256 加密 + 证书互换全链路可测），真机接入前不需要等它。
>
> 无法自动化的部分：TIA 图形界面操作与"允许外部应用访问"确认对话框需人工点击；
> 下方给出精确操作路径，Openness 自动化脚本见 `tia_openness_opcua.ps1`。

## 一、生成并互换证书（边缘侧已自动化）

```bash
python -m services.edge_collector.certs --out services/edge_collector/certs
# 产出: client_cert.der / client_key.pem（边缘采集器身份）
#       server_cert.der / server_key.pem（仿真服务器用；真机场景由 PLC 提供）
```

互换流程（真机）：
1. **边缘证书 → PLC**：TIA 中 `项目树 → 安全设置 → 证书管理器 → 受信证书 → 导入`，
   选择 `services/edge_collector/certs/client_cert.der`；然后在 CPU 的
   `OPC UA → 服务器 → 安全 → 受信客户端` 中勾选该证书
2. **PLC 证书 → 边缘**：证书管理器中右键 CPU 的 OPC UA 服务器证书 → `导出`，
   存为 `services/edge_collector/certs/server_cert.der`（覆盖仿真证书）

## 二、启用 OPC UA 服务器（TIA GUI 精确路径）

1. `项目树 → 设备 → CPU(如 PLC_1) → 属性 → OPC UA → 服务器`：勾选 **激活 OPC UA 服务器**
2. 同页 `运行系统许可证`：选择 **OPC UA**（S7-1500 标准 CPU 自带，无需额外订货号；
   旧固件 CPU 需确认许可证类型）
3. `OPC UA → 服务器 → 端点`：确认端点 `opc.tcp://<CPU IP>:4840`，
   安全策略勾选 **Basic256Sha256 - SignAndEncrypt**（与边缘采集器一致）
4. `OPC UA → 服务器 → 常规 → 会话/订阅上限`：M1 规模（1 客户端、~30 监测项）
   默认值足够；`最短发布间隔` 保持 100ms 即可
5. `OPC UA → 服务器 → 可达的 DB`：勾选要暴露的数据块——**必须包含
   `IO_HMI`、`IF_HMI` 及 tags_weld.yaml 中所有 TODO 节点所在的 DB**（SICAR 中
   多为全局 DB；非优化访问的 DB 才能按符号+偏移混合寻址）
6. 编译 → 下载到 CPU（PLCSIM Advanced 则下载到虚拟 CPU）

## 三、用 UaExpert 浏览确认节点（冻结标签表）

1. UaExpert 添加服务器：`opc.tcp://<CPU IP>:4840`，安全策略 Basic256Sha256，
   证书选择 `certs/client_cert.der/key`（首次连接会被拒，把 UaExpert 证书也在
   TIA 中受信，或在 CPU 属性里把"不受信客户端证书的处理"设为自动拒绝前先记录）
2. 浏览 `Objects → <DB 名>`，逐一核对 `services/edge_collector/tags_weld.yaml`
   中带 `TODO(TIA)` 的路径是否存在；确认后**把 TODO 值改为实际路径并提交仓库冻结**，
   标签表即成为与 PLC 的正式接口契约

## 四、端到端验收

```bash
python -m services.edge_collector.app \
    --endpoint opc.tcp://<CPU IP>:4840 \
    --user <opcua用户> --password <***> \
    --mqtt-host <broker> 
# 观察: GET /api/v1/devices 遥测行数持续增长；RUL 预警工单正常触发
```

## 五、（可选）Openness 自动化

`deployment/tia_openness_opcua.ps1`：在 TIA 打开项目的情况下，通过
Siemens.Engineering API 读取 CPU 的 OPC UA 配置状态并导出端点信息。
首次运行 TIA 会弹出"允许外部应用访问"对话框——**这一步必须人工点击允许**，
因此该脚本只做只读巡检+证书导入辅助，不做无人值守修改。
