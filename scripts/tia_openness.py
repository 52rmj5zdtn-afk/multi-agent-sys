"""tia_openness.py — TIA Openness 巡检与配置脚本（pythonnet 驱动）

用法（dl 环境，python.exe 已加入 Openness 白名单）:
    python scripts/tia_openness.py inventory    # 只读巡检: CPU/OPC UA/DB 清单 → JSON
    python scripts/tia_openness.py opcua-on     # 启用 OPC UA 服务器 + 设置安全策略
    python scripts/tia_openness.py download     # 下载到 PLCSIM Advanced 虚拟 PLC
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
CERTS_DIR = Path(__file__).resolve().parent.parent / "services" / "edge_collector" / "certs"
INVENTORY = Path(__file__).resolve().parent.parent / "deployment" / "tia_inventory.json"

import os
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402
clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))

from Siemens.Engineering import TiaPortal  # noqa: E402


def attach():
    procs = TiaPortal.GetProcesses()
    if not procs or procs.Count == 0:
        raise RuntimeError("未发现 TIA 进程")
    for proc in procs:
        try:
            path = proc.ProjectPath
            print(f"TIA 进程: ProjectPath={path} Mode={proc.Mode}")
            if path:
                tia = proc.Attach()          # 白名单内应用直接附加，无对话框
                return tia, tia.Projects[0]
        except Exception as e:  # noqa: BLE001
            print(f"  进程不可访问: {e}")
    raise RuntimeError("所有 TIA 进程均无打开项目（检查白名单/权限对话框）")


def find_cpu(project):
    """设备项名含 1516/1517/1518（S7-1500 CPU），或子项含 OPC UA 的即 CPU。"""
    def walk(item):
        name = ""
        try:
            name = item.Name
        except Exception:
            pass
        if any(k in name for k in ("1516", "1517", "1518", "1515")) and "PLC" in name.upper():
            return item
        try:
            for sub in item.DeviceItems:
                r = walk(sub)
                if r is not None:
                    return r
        except Exception:
            pass
        return None

    for dev in project.Devices:
        r = walk(dev)
        if r is not None:
            return r
    return None


def get_software(cpu_item):
    """DeviceItem → SoftwareContainer → PlcSoftware（S7-1500 程序容器）。"""
    try:
        from Siemens.Engineering.SW import SoftwareContainer
        sc = cpu_item.GetService(SoftwareContainer)
        return sc.Software if sc else None
    except Exception:
        try:
            return cpu_item.Software
        except Exception:
            return None


def scalar_props(obj, label, out):
    out.append(f"=== {label} :: {obj.GetType().FullName}")
    for prop in obj.GetType().GetProperties():
        if prop.GetIndexParameters():
            continue
        try:
            val = prop.GetValue(obj, None)
            if val is None:
                out.append(f"  {prop.Name} = <null>")
            elif val.GetType().IsPrimitive or val.GetType() == str or val.GetType().IsEnum:
                out.append(f"  {prop.Name} = {val}")
            else:
                out.append(f"  {prop.Name} = <{val.GetType().Name}>")
        except Exception:
            out.append(f"  {prop.Name} = <error>")


def cmd_inventory(_cpu=None):
    tia, project = attach()
    lines: list[str] = [f"PROJECT: {project.Name} | Path: {project.Path}"]
    data: dict = {"project": project.Name, "devices": [], "cpu": {}}

    cpu = find_cpu(project)
    if cpu is None:
        raise RuntimeError("未找到 S7-1500 CPU 设备项")
    lines.append(f"CPU ITEM: {cpu.Name} :: {cpu.GetType().FullName}")
    data["cpu"]["name"] = cpu.Name
    data["cpu"]["type"] = cpu.GetType().Name

    # 订货号/固件（属性 API）
    for attr in ("TypeIdentifier", "ArticleNumber", "FirmwareVersion", "Name"):
        try:
            v = cpu.GetAttribute(attr)
            lines.append(f"  cpu.{attr} = {v}")
            data["cpu"][attr] = str(v)
        except Exception as e:  # noqa: BLE001
            lines.append(f"  cpu.{attr} = <err {e}>")

    # OPC UA_1 子项（OPC UA 服务器配置实体）——枚举其全部属性
    def dump_attrs(obj, label):
        try:
            infos = obj.GetAttributeInfos()
        except Exception as e:  # noqa: BLE001
            lines.append(f"{label}: GetAttributeInfos <err {e}>")
            return
        for info in infos:
            try:
                v = obj.GetAttribute(info.Name)
                lines.append(f"  {label}.{info.Name} = {v}")
                data["cpu"].setdefault(label, {})[info.Name] = str(v)
            except Exception as e:  # noqa: BLE001
                lines.append(f"  {label}.{info.Name} = <err {e}>")

    opcua_item = None
    try:
        for sub in cpu.DeviceItems:
            if "OPC UA" in (sub.Name or ""):
                opcua_item = sub
                break
    except Exception:
        pass
    if opcua_item is not None:
        lines.append(f"OPC UA ITEM: {opcua_item.Name}")
        dump_attrs(opcua_item, "opcua_item")

    # PLC 软件容器 → DB 清单 + SW.OpcUa
    sw = get_software(cpu)
    if sw is not None:
        lines.append(f"SOFTWARE: {sw.GetType().FullName}")
        data["cpu"]["software_type"] = sw.GetType().Name
        try:
            blocks = []
            for b in sw.BlockGroup.Blocks:
                blocks.append(b.Name)
                if len(blocks) <= 80:
                    lines.append(f"DB: {b.Name}")
            data["cpu"]["blocks"] = blocks
        except Exception as e:  # noqa: BLE001
            lines.append(f"BLOCKS = <err {e}>")
        try:
            opcua = sw.OpcUa
            lines.append(f"SW.OPCUA: {opcua.GetType().FullName}")
            for info in opcua.GetAttributeInfos():
                try:
                    v = opcua.GetAttribute(info.Name)
                    lines.append(f"  sw.opcua.{info.Name} = {v}")
                    data["cpu"].setdefault("sw_opcua", {})[info.Name] = str(v)
                except Exception as e:  # noqa: BLE001
                    lines.append(f"  sw.opcua.{info.Name} = <err {e}>")
        except Exception as e:  # noqa: BLE001
            lines.append(f"SW.OPCUA = <err {e}>")
    else:
        lines.append("SOFTWARE = <none>")

    text = "\n".join(lines)
    INVENTORY.write_text(text, encoding="utf-8")
    (INVENTORY.with_suffix(".structured.json")).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(text[:5000])
    print(f"\n[written] {INVENTORY}")


def cmd_opcua_on(_cpu=None):
    tia, project = attach()
    cpu = find_cpu(project)
    sw = cpu.Software
    opcua = sw.OpcUa
    changed = {}
    # 常见属性名按 V20 实际属性清单匹配（先用 inventory 确认）
    candidates = {
        "ServerEnabled": True,
        "ActivateServer": True,
        "Enabled": True,
    }
    names = [i.Name for i in opcua.GetAttributeInfos()]
    for attr, val in candidates.items():
        if attr in names:
            opcua.SetAttribute(attr, val)
            changed[attr] = val
    if not changed:
        print("未找到启用属性，可用属性:", names)
        return
    project.Save()
    print("已启用 OPC UA 并保存:", changed)


def cmd_download(_cpu=None):
    raise SystemExit("download 子命令在确认 inventory 结果后实现")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "inventory"
    {"inventory": cmd_inventory, "opcua-on": cmd_opcua_on, "download": cmd_download}[cmd]()
