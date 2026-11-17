"""对比主 PLC(编译通过) 与 MiniPLC 的 OPC UA/保护/许可证相关属性值。"""
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

KEYS = ("OpcUa", "Certificate", "AccessControl", "License", "Protection",
        "Confidential", "WebserverActivate", "PutGet")


def attach():
    for proc in TiaPortal.GetProcesses():
        try:
            tia = proc.Attach()
            if tia.Projects.Count > 0:
                return tia, tia.Projects[0]
        except Exception:
            continue
    raise RuntimeError("no TIA process with open project")


def collect(item, out):
    try:
        for info in item.GetAttributeInfos():
            if any(k.lower() in info.Name.lower() for k in KEYS):
                try:
                    v = item.GetAttribute(info.Name)
                    out[info.Name] = str(v)[:90]
                except Exception:
                    pass
    except Exception:
        pass
    try:
        for s in item.DeviceItems:
            collect(s, out)
    except Exception:
        pass


def main() -> None:
    tia, project = attach()
    main_dev = next(d for d in project.Devices
                    if any("Zone1_PLC1516TF" in (i.Name or "") for i in d.DeviceItems))
    mini = next(d for d in project.Devices
                if any("MiniPLC" in (i.Name or "") for i in d.DeviceItems))
    a, b = {}, {}
    collect(main_dev, a)
    collect(mini, b)
    keys = sorted(set(a) | set(b))
    print(f"{'attribute':52s} MAIN vs MiniPLC")
    for k in keys:
        va, vb = a.get(k, "<无>"), b.get(k, "<无>")
        mark = "   " if va == vb else " * "
        print(f"{mark}{k:50s} {va[:38]:38s} | {vb[:38]}")


if __name__ == "__main__":
    main()
