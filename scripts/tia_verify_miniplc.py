"""校验 MiniPLC 块清单 + DBAccessibleFromOPCUA 值，清理重复。"""
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
    tia, project = attach()
    mini = next((d for d in project.Devices if "MiniPLC" in (d.Name or "")), None)
    SC = find_type("SoftwareContainer")
    holder = []

    def walk(item):
        if holder:
            return
        try:
            sc = item.GetService[SC]()
            if sc is not None and sc.Software is not None:
                holder.append(sc.Software)
                return
        except Exception:
            pass
        try:
            for sub in item.DeviceItems:
                walk(sub)
        except Exception:
            pass

    walk(mini)
    sw = holder[0]
    doomed = []
    for b in sw.BlockGroup.Blocks:
        nm = b.Name
        opc = None
        try:
            opc = b.GetAttribute("DBAccessibleFromOPCUA")
        except Exception:
            pass
        print(f"  block {nm} type={b.GetType().Name} opcua={opc}")
        if nm in ("WELD_DB_1", "LINE_DB_1", "WELD_DB_2", "LINE_DB_2"):
            doomed.append((nm, b))
    for nm, b in doomed:
        try:
            b.Delete()
            print("deleted duplicate", nm)
        except Exception as e:
            print("dup delete fail:", str(e)[:120])
    if doomed:
        project.Save()
        print("saved")


if __name__ == "__main__":
    main()
