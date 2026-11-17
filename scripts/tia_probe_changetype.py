"""探测 DeviceItem.ChangeType 签名，并尝试把 F-CPU 换成同固件标准 CPU。"""
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
    cpu = find_cpu_item(project)
    print("cpu:", cpu.Name)
    for attr in ("TypeIdentifier", "ArticleNumber", "FirmwareVersion"):
        try:
            print(f"  {attr} =", cpu.GetAttribute(attr))
        except Exception as e:
            print(f"  {attr} err:", str(e)[:100])

    ct = None
    for m in cpu.GetType().GetMethods():
        if m.Name == "ChangeType":
            ct = m
            ps = [f"{p.ParameterType.Name} {p.Name}" for p in m.GetParameters()]
            print("ChangeType(", ", ".join(ps), ")")
    if ct is None:
        raise SystemExit("no ChangeType")

    # 打印当前 TypeIdentifier 作为参照格式
    ident = str(cpu.GetAttribute("TypeIdentifier"))
    print("current identifier:", ident)


if __name__ == "__main__":
    main()
