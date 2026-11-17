"""切换 MiniPLC 的 OpcUaServer 开关（诊断用）。用法: tia_opcua_toggle.py on|off"""
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


def find_type(name):
    for t in asm_types():
        if t is not None and t.Name == name:
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
    val = sys.argv[1].lower() == "on"
    tia, project = attach()
    mini = next((d for d in project.Devices if any("MiniPLC" in (i.Name or "") for i in d.DeviceItems)), None)
    items = []

    def walk(item):
        try:
            if "OPC UA" in (item.Name or ""):
                items.append(item)
        except Exception:
            pass
        try:
            for s in item.DeviceItems:
                walk(s)
        except Exception:
            pass

    walk(mini)
    for it in items:
        it.SetAttribute("OpcUaServer", val)
        print("OpcUaServer =", it.GetAttribute("OpcUaServer"))
    project.Save()


if __name__ == "__main__":
    main()
