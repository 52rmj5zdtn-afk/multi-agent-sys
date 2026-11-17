"""探测：1) 删除非OB安全块是否同样被权限拦截 2) Openness 是否有 ChangeDevice/SwapDevice API。"""
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

SC_TYPE = None
try:
    types = ASM.GetTypes()
except Exception as e:
    types = e.Types
for t in types:
    if t is not None and t.Name == "SoftwareContainer":
        SC_TYPE = t


def attach():
    for proc in TiaPortal.GetProcesses():
        try:
            tia = proc.Attach()
            if tia.Projects.Count > 0:
                return tia, tia.Projects[0]
        except Exception:
            continue
    raise RuntimeError("no TIA process with open project")


def find_cpu_item(project):
    found = []

    def walk(item):
        try:
            nm = item.Name or ""
            if "1516" in nm and "PLC" in nm.upper():
                found.append(item)
                return
        except Exception:
            pass
        try:
            for sub in item.DeviceItems:
                walk(sub)
        except Exception:
            pass

    for dev in project.Devices:
        if not found:
            walk(dev)
    return found[0] if found else None


def main() -> None:
    tia, project = attach()
    print("project:", project.Path)
    cpu = find_cpu_item(project)
    dev = None
    try:
        dev = cpu.Parent
    except Exception:
        pass

    # ---- 反射: Change/Swap 相关 API ----
    for obj, label in ((cpu, "DeviceItem"), (dev, "Parent")):
        if obj is None:
            continue
        ms = sorted({m.Name for m in obj.GetType().GetMethods()
                     if any(k in m.Name for k in ("Change", "Swap", "Replace", "Convert"))})
        print(f"{label}({obj.GetType().Name}) change-ish methods: {ms}")

    # ---- 尝试删除一个 F-DB（非OB）----
    sw = cpu.GetService[SC_TYPE]().Software
    sg = None
    for g in sw.BlockGroup.Groups:
        if "SAFETY" in (g.Name or "").upper() or "安全" in g.Name:
            sg = g
    victims = []

    def collect(g):
        for b in g.Blocks:
            victims.append(b)
        for sub in g.Groups:
            collect(sub)

    collect(sg)
    from collections import Counter
    print("safety block types:", Counter(v.GetType().Name for v in victims))
    db = next((v for v in victims if v.GetType().Name == "DB"), victims[0])
    print("attempt delete:", db.GetType().Name, db.Name)
    try:
        db.Delete()
        print("DELETE OK (无权限拦截!)")
    except Exception as e:
        print("DELETE BLOCKED:", str(e)[:300])


if __name__ == "__main__":
    main()
