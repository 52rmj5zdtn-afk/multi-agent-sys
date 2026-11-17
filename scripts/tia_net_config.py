"""MiniPLC 网络配置：X1 设静态 IP 192.168.1.6/24；恢复 OpcUaServer=True；打印节点属性。"""
from __future__ import annotations

import sys
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
import os
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402
ASM = clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))
from Siemens.Engineering import TiaPortal  # noqa: E402


def asm_types():
    try:
        return ASM.GetTypes()
    except Exception as e:
        return e.Types


def find_type(fullname):
    for t in asm_types():
        if t is not None and t.FullName == fullname:
            return t
    return None


def attach():
    for proc in TiaPortal.GetProcesses():
        try:
            tia = proc.Attach()
            if tia.Projects.Count > 0:
                return tia, tia.Projects[0]
        except Exception:
            continue
    raise RuntimeError("no TIA process with open project")


def main() -> None:
    tia, project = attach()
    mini = next((d for d in project.Devices
                 if any("MiniPLC" in (i.Name or "") for i in d.DeviceItems)), None)

    # 1) OpcUaServer 恢复
    opc = []

    def walk_opc(item):
        try:
            if "OPC UA" in (item.Name or ""):
                opc.append(item)
        except Exception:
            pass
        try:
            for s in item.DeviceItems:
                walk_opc(s)
        except Exception:
            pass

    walk_opc(mini)
    for it in opc:
        it.SetAttribute("OpcUaServer", True)
        print("OpcUaServer =", it.GetAttribute("OpcUaServer"))

    # 2) X1 网络节点
    x1 = []
    NI = find_type("Siemens.Engineering.HW.Features.NetworkInterface")
    print("NetworkInterface type:", NI)

    def walk_x1(item):
        try:
            if (item.GetAttribute("Label") or "") == "X1":
                x1.append(item)
        except Exception:
            pass
        try:
            for s in item.DeviceItems:
                walk_x1(s)
        except Exception:
            pass

    walk_x1(mini)
    for it in x1:
        print("X1 item:", it.Name)
        svc = it.GetService[NI]()
        print("  service:", svc)
        if svc is None:
            continue
        nodes = svc.Nodes
        print("  nodes:", nodes.Count)
        for n in nodes:
            for info in n.GetAttributeInfos():
                if any(k in info.Name for k in ("Ip", "Subnet", "Address", "Name")):
                    try:
                        print(f"    node.{info.Name} =", n.GetAttribute(info.Name))
                    except Exception as e:
                        print(f"    node.{info.Name} err", str(e)[:60])
            for attr, val in (("Address", "192.168.1.6"), ("SubnetMask", "255.255.255.0")):
                try:
                    n.SetAttribute(attr, val)
                    print(f"    set {attr} = {val}")
                except Exception as e:
                    print(f"    set {attr} fail:", str(e)[:120])

    project.Save()
    print("saved.")


if __name__ == "__main__":
    main()
