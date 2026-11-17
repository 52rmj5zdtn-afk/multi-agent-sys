"""尝试删除 MiniPLC 的自动安全结构（其安全程序无密码，可能无需权限）。"""
from __future__ import annotations

import sys
import time
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
    mini = next((d for d in project.Devices
                 if any("MiniPLC" in (i.Name or "") for i in d.DeviceItems)), None)
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
            for s in item.DeviceItems:
                walk(s)
        except Exception:
            pass

    walk(mini)
    sw = holder[0]
    targets = []
    for b in list(sw.BlockGroup.Blocks):
        nm = b.Name
        if nm in ("FOB_RTG1", "Main_Safety_RTG1", "Main_Safety_RTG1_DB"):
            targets.append((nm, b))
    print("targets:", [t[0] for t in targets])
    for nm, b in targets:
        try:
            b.Delete()
            print("deleted:", nm)
            time.sleep(1)
        except Exception as e:
            print("delete FAIL:", nm, "|", str(e)[:250])
            break
    project.Save()
    print("saved")


if __name__ == "__main__":
    main()
