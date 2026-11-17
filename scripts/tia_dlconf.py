"""tia_dlconf.py — dump DownloadProvider 的全部连接模式/接口/目标。"""
from __future__ import annotations

import os
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402

clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))
import System  # noqa: E402
from Siemens.Engineering import TiaPortal  # noqa: E402

ASM = System.Reflection.Assembly.LoadFrom(str(API_DIR / "Siemens.Engineering.dll"))


def t(name: str):
    return ASM.GetType(name)


def gs(obj, type_name):
    ctype = t(type_name)
    m = obj.GetType().GetMethod("GetService")
    if m is None or ctype is None:
        return None
    try:
        return m.MakeGenericMethod(ctype).Invoke(obj, None)
    except Exception:
        return None


def attach():
    for proc in TiaPortal.GetProcesses():
        if proc.ProjectPath:
            tia = proc.Attach()
            return tia, tia.Projects[0]
    raise RuntimeError("TIA 无打开项目")


def main() -> None:
    tia, project = attach()
    for dev in project.Devices:
        stack = list(dev.DeviceItems)
        while stack:
            it = stack.pop()
            try:
                stack.extend(list(it.DeviceItems))
            except Exception:
                pass
            if "Zone1_PLC1516TF" not in (it.Name or ""):
                continue
            dp = gs(it, "Siemens.Engineering.Download.DownloadProvider")
            conf = dp.Configuration
            print(f"device item: {it.Name}")
            for mode in conf.Modes:
                print(f"  mode: {mode.Name}")
                for pcif in mode.PcInterfaces:
                    print(f"    pcif: {pcif.Name}")
                    for tgt in pcif.TargetInterfaces:
                        addrs = []
                        try:
                            for a in tgt.Addresses:
                                addrs.append(str(a))
                        except Exception:
                            pass
                        print(f"      tgt: {tgt.Name} addresses={addrs}")


if __name__ == "__main__":
    main()
