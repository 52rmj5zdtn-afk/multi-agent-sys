"""tia_change_cpu.py — 将主 PLC CPU 更改为旧版订货号（PLCSIM Advanced V7.0 原版可加载）。

用法: python scripts/tia_change_cpu.py [目标TypeIdentifier]
默认: OrderNumber:6ES7 516-3UN00-0AB0/V4.0  (CPU 1516TF-3 PN/DP, FW V4.0 初期目录)

注意: 会修改当前打开的项目（用户在 TIA 中可见）。安全程序需重新编译。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402

clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))
from Siemens.Engineering import TiaPortal  # noqa: E402


def attach():
    for proc in TiaPortal.GetProcesses():
        if proc.ProjectPath:
            tia = proc.Attach()
            return tia, tia.Projects[0]
    raise RuntimeError("TIA 无打开项目")


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "OrderNumber:6ES7 516-3UN00-0AB0/V4.0"
    tia, project = attach()
    print(f"project: {project.Name}")
    for dev in project.Devices:
        stack = list(dev.DeviceItems)
        while stack:
            it = stack.pop()
            try:
                stack.extend(list(it.DeviceItems))
            except Exception:
                pass
            try:
                nm = it.Name
            except Exception:
                nm = ""
            if "PLC1516" not in (nm or ""):
                continue
            old = it.GetAttribute("TypeIdentifier")
            print(f"current: {nm} -> {old}")
            print(f"changing to: {target}")
            try:
                it.ChangeType(target)
                new = it.GetAttribute("TypeIdentifier")
                print(f"new TypeIdentifier: {new}")
            except Exception as e:
                print(f"ChangeType FAILED: {type(e).__name__}: {str(e)[:400]}")
                return
    print("DONE-CHANGE")


if __name__ == "__main__":
    main()
