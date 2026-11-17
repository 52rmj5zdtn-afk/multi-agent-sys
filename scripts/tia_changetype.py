"""尝试 ChangeType: F-CPU 1516TF → 标准 CPU（1516T / 1516），观察安全程序是否被自动移除。"""
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


def count_safety(sw):
    sg = None
    for g in sw.BlockGroup.Groups:
        if "SAFETY" in (g.Name or "").upper() or "安全" in g.Name:
            sg = g
    if sg is None:
        return 0

    n = [0]

    def collect(g):
        n[0] += g.Blocks.Count
        for sub in g.Groups:
            collect(sub)

    collect(sg)
    return n[0]


def main() -> None:
    candidates = sys.argv[1:] or [
        "OrderNumber:6ES7 516-3TP10-0AB0/V4.0",
        "OrderNumber:6ES7 516-3AP10-0AB0/V4.0",
    ]
    tia, project = attach()
    cpu = find_cpu_item(project)
    sw = None
    types = None
    try:
        types = ASM.GetTypes()
    except Exception as e:
        types = e.Types
    for t in types:
        if t is not None and t.Name == "SoftwareContainer":
            sw = cpu.GetService[t]().Software
    print("safety blocks before:", count_safety(sw))

    for cand in candidates:
        print("try ChangeType:", cand)
        try:
            cpu.ChangeType(cand)
            print("  ChangeType OK ->", cpu.GetAttribute("TypeIdentifier"))
            break
        except Exception as e:
            print("  FAIL:", str(e)[:400])
            time.sleep(1)
    else:
        raise SystemExit("all candidates failed")

    # 结果观察
    time.sleep(2)
    try:
        print("safety blocks after:", count_safety(sw))
    except Exception as e:
        print("count after err:", str(e)[:200])
    # 保存副本
    project.Save()
    print("saved.")


if __name__ == "__main__":
    main()
