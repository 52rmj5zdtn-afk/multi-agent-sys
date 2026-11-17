"""枚举 PLC 程序块（含组路径/语言/编号），识别安全程序结构。只读。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
OUT = Path(__file__).resolve().parent.parent / "deployment" / "tia_blocks_inventory.json"

import os
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402
clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))

from Siemens.Engineering import TiaPortal  # noqa: E402


def attach():
    procs = TiaPortal.GetProcesses()
    for proc in procs:
        try:
            tia = proc.Attach()
            if tia.Projects.Count > 0:
                return tia, tia.Projects[0]
        except Exception:
            continue
    raise RuntimeError("no TIA process with open project")


def get_software(cpu_item):
    """通过 GetServices() 找 SoftwareContainer（避免 pythonnet 嵌套类导入问题）。"""
    try:
        svcs = cpu_item.GetServices()
    except Exception:
        return None
    for s in svcs:
        try:
            if "SoftwareContainer" in s.GetType().FullName:
                return s.Software
        except Exception:
            continue
    return None


def find_cpus(project):
    cpus = []

    def walk(item, dev_name):
        try:
            name = item.Name
        except Exception:
            name = ""
        type_name = ""
        try:
            type_name = item.TypeName or ""
        except Exception:
            pass
        hay = f"{name}|{type_name}"
        if ("1516" in hay or "1517" in hay or "1518" in hay) and "PLC" in hay.upper():
            cpus.append((dev_name, item))
        try:
            for sub in item.DeviceItems:
                walk(sub, dev_name)
        except Exception:
            pass

    for dev in project.Devices:
        walk(dev, dev.Name)
    return cpus


def block_info(b, group_path):
    info = {"name": b.Name, "group": group_path}
    for attr in ("Number", "ProgrammingLanguage", "TypeName", "AutoNumber",
                 "IsKnowHowProtected", "IsSafetyBlock", "IsFailsafe", "IsFBlock",
                 "HeaderFamily", "HeaderVersion"):
        try:
            v = getattr(b, attr)
            if v is not None and not callable(v):
                info[attr] = str(v)
        except Exception:
            pass
    return info


def walk_group(group, path, out):
    cur = f"{path}/{group.Name}" if path else group.Name
    for b in group.Blocks:
        try:
            out.append(block_info(b, cur))
        except Exception as e:
            out.append({"name": f"<err {e}>", "group": cur})
    for g in group.Groups:
        walk_group(g, cur, out)


def main():
    tia, project = attach()
    print("project:", project.Path, project.Name)
    cpus = find_cpus(project)
    print("cpus:", [(d, i.Name) for d, i in cpus])
    all_blocks = []
    for dev_name, cpu in cpus:
        sw = get_software(cpu)
        if sw is None:
            print("no software on", cpu.Name)
            continue
        blocks = []
        walk_group(sw.BlockGroup, "", blocks)
        all_blocks.append({"device": dev_name, "cpu": cpu.Name, "blocks": blocks})
        print(f"{cpu.Name}: {len(blocks)} blocks")
        # 组统计
        from collections import Counter
        groups = Counter(b["group"] for b in blocks)
        for g, n in sorted(groups.items()):
            print(f"  group {g}: {n}")
    OUT.write_text(json.dumps(all_blocks, ensure_ascii=False, indent=1))
    print("saved:", OUT)


if __name__ == "__main__":
    main()
