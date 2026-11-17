"""把主 PLC 的通信/OPC UA 证书指派给 MiniPLC；许可证=Large；GBIT_3 改子网；重编译看剩余错误。"""
from __future__ import annotations

import sys
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
import os
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402
ASM = clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))
import System  # noqa: E402
from Siemens.Engineering import TiaPortal  # noqa: E402
from Siemens.Engineering.HW import OpcUaPurchasedLicense  # noqa: E402,F401


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


def get_cpu(dev, name):
    return next(i for i in dev.DeviceItems if (i.Name or "") == name)


def find_item(dev, pred):
    out = []

    def walk(item):
        try:
            if pred(item):
                out.append(item)
        except Exception:
            pass
        try:
            for s in item.DeviceItems:
                walk(s)
        except Exception:
            pass

    walk(dev)
    return out


def main() -> None:
    tia, project = attach()
    main_dev = next(d for d in project.Devices
                    if any("Zone1_PLC1516TF" in (i.Name or "") for i in d.DeviceItems))
    mini = next(d for d in project.Devices
                if any("MiniPLC" in (i.Name or "") for i in d.DeviceItems))
    main_cpu = get_cpu(main_dev, "Zone1_PLC1516TF")
    mini_cpu = get_cpu(mini, "MiniPLC")

    # 1) 许可证 Large
    try:
        mini_cpu.SetAttribute("OpcUaPurchasedLicense",
                              OpcUaPurchasedLicense.SimaticOpcUaS71500Large)
        print("license ->", mini_cpu.GetAttribute("OpcUaPurchasedLicense"))
    except Exception as e:
        print("license fail:", str(e)[:150])

    # 2) 证书复制
    try:
        cert = main_cpu.GetAttribute("PlcCommunicationCertificate")
        print("main PlcCommunicationCertificate:", cert)
        if cert is not None:
            mini_cpu.SetAttribute("PlcCommunicationCertificate", cert)
            print("assigned PlcCommunicationCertificate ->", mini_cpu.GetAttribute("PlcCommunicationCertificate"))
    except Exception as e:
        print("comm cert fail:", str(e)[:200])

    opc_main = find_item(main_dev, lambda i: "OPC UA" in (i.Name or ""))
    opc_mini = find_item(mini, lambda i: "OPC UA" in (i.Name or ""))
    if opc_main and opc_mini:
        try:
            c2 = opc_main[0].GetAttribute("OpcUaServerCertificate")
            print("main OpcUaServerCertificate:", c2)
            if c2 is not None:
                opc_mini[0].SetAttribute("OpcUaServerCertificate", c2)
                print("assigned ->", opc_mini[0].GetAttribute("OpcUaServerCertificate"))
        except Exception as e:
            print("opc cert fail:", str(e)[:200])

    # 3) GBIT_3 子网
    NI = find_type("Siemens.Engineering.HW.Features.NetworkInterface")

    def walk(item):
        try:
            if (item.GetAttribute("Label") or "") == "X3" or "GBIT" in (item.Name or ""):
                svc = item.GetService[NI]()
                if svc:
                    for n in svc.Nodes:
                        n.SetAttribute("Address", "192.168.3.1")
                        print("GBIT ->", n.GetAttribute("Address"))
        except Exception:
            pass
        try:
            for s in item.DeviceItems:
                walk(s)
        except Exception:
            pass

    walk(mini)

    # 4) 编译
    ICOMP = find_type("Siemens.Engineering.Compiler.ICompilable")
    res = mini_cpu.GetService[ICOMP]().Compile()
    msgs = []

    def w(m):
        try:
            if m.Description:
                msgs.append((str(m.State), m.Description))
        except Exception:
            pass
        try:
            for s in m.Messages:
                w(s)
        except Exception:
            pass

    for m in res.Messages:
        w(m)
    print(f"=== {len(msgs)} 条消息 ===")
    for st, d in msgs:
        print(f"  [{st}] {d}")
    project.Save()


if __name__ == "__main__":
    main()
