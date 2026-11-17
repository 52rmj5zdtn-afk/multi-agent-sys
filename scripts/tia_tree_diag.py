"""诊断：打印 PLC 设备树的每个 DeviceItem 及其服务类型。"""
from __future__ import annotations

import sys
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
import os
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402
clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))

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


def main():
    tia, project = attach()
    for dev in project.Devices:
        print(f"DEVICE: {dev.Name}")

        def walk(item, depth):
            if depth > 6:
                return
            indent = "  " * depth
            name = ""
            type_name = ""
            try:
                name = item.Name
            except Exception:
                pass
            try:
                type_name = item.TypeName or ""
            except Exception:
                pass
            svcs = []
            try:
                for s in item.GetServices():
                    try:
                        svcs.append(s.GetType().Name)
                    except Exception:
                        svcs.append("?")
            except Exception as e:
                svcs.append(f"<err {e}>")
            print(f"{indent}- {name} [{type_name}] services={svcs}")
            try:
                for sub in item.DeviceItems:
                    walk(sub, depth + 1)
            except Exception:
                pass

        walk(dev, 0)


if __name__ == "__main__":
    main()
