"""Dump MiniPLC CPU / PROFINET 接口_1 / 读卡器 的全部属性，找存储卡与 IP 配置。"""
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


def dump(item, label, filt=None):
    print(f"=== {label} ({item.Name}) ===")
    try:
        infos = item.GetAttributeInfos()
    except Exception as e:
        print("  infos err:", str(e)[:100])
        return
    for info in infos:
        if filt and not any(k.lower() in info.Name.lower() for k in filt):
            continue
        try:
            v = item.GetAttribute(info.Name)
            print(f"  {info.Name} = {v}")
        except Exception as e:
            print(f"  {info.Name} = <err {str(e)[:60]}>")


def main() -> None:
    tia, project = attach()
    mini = next((d for d in project.Devices
                 if any("MiniPLC" in (i.Name or "") for i in d.DeviceItems)), None)
    targets = {}

    def walk(item):
        nm = item.Name or ""
        if nm in ("MiniPLC", "PROFINET 接口_1", "PROFINET 接口_2",
                  "卡读卡器/写卡器_1", "CPU 显示_1", "OPC UA_1"):
            targets[nm] = item
        try:
            for s in item.DeviceItems:
                walk(s)
        except Exception:
            pass

    walk(mini)
    for nm, it in targets.items():
        dump(it, nm)


if __name__ == "__main__":
    main()
