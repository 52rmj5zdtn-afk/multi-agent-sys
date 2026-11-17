"""定位 SoftwareContainer 类型 → 枚举块组树，标记安全块。"""
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
types = None
try:
    types = ASM.GetTypes()
except Exception as e:
    try:
        types = e.Types
    except Exception:
        types = None
if types is None:
    raise SystemExit("cannot enumerate assembly types")
for t in types:
    if t is not None and t.Name == "SoftwareContainer":
        SC_TYPE = t
        print("SoftwareContainer:", t.FullName)
if SC_TYPE is None:
    raise SystemExit("SoftwareContainer type not found in assembly")


def attach():
    for proc in TiaPortal.GetProcesses():
        try:
            tia = proc.Attach()
            if tia.Projects.Count > 0:
                return tia, tia.Projects[0]
        except Exception:
            continue
    raise RuntimeError("no TIA process with open project")


def main():
    tia, project = attach()
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
    if not found:
        raise SystemExit("CPU not found")
    cpu = found[0]
    print("CPU item:", cpu.Name)

    sc = cpu.GetService[SC_TYPE]()
    print("SC:", sc)
    sw = sc.Software
    print("SW:", sw.GetType().FullName)

    def walk_group(g, path, stats):
        cur = f"{path}/{g.Name}" if path else g.Name
        n = 0
        try:
            n = g.Blocks.Count
        except Exception:
            pass
        stats.append((cur, n))
        try:
            for sub in g.Groups:
                walk_group(sub, cur, stats)
        except Exception:
            pass

    stats = []
    walk_group(sw.BlockGroup, "", stats)
    for cur, n in stats:
        print(f"GROUP {cur}: {n} blocks")

    # 打印疑似安全块（名称特征）及每个组的前几个块名
    def show_group(g, path):
        cur = f"{path}/{g.Name}" if path else g.Name
        try:
            names = [b.Name for b in g.Blocks]
        except Exception:
            names = []
        f_like = [x for x in names if x.upper().startswith(("F_", "F-", "SF_")) or "SAFETY" in x.upper()]
        if f_like:
            print(f"  F-LIKE in {cur}: {f_like[:20]}")
        try:
            for sub in g.Groups:
                show_group(sub, cur)
        except Exception:
            pass

    show_group(sw.BlockGroup, "")


if __name__ == "__main__":
    main()
