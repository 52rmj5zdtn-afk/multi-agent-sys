"""反射查找编译 API + X1 网络接口属性（IP/子网），并尝试硬件编译拿错误消息。"""
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


def compile_like(obj, label):
    ms = [m for m in obj.GetType().GetMethods() if "Compile" in m.Name]
    out = []
    for m in ms:
        ps = ",".join(p.ParameterType.Name for p in m.GetParameters())
        out.append(f"{m.Name}({ps})")
    print(f"[compile] {label}({obj.GetType().Name}): {sorted(set(out))}")
    return ms


def main() -> None:
    tia, project = attach()
    mini = next((d for d in project.Devices if "MiniPLC" in (d.Name or "")), None)
    compile_like(project, "project")
    compile_like(mini, "device")

    x1 = None
    cpu_item = None
    for it in mini.DeviceItems:
        compile_like(it, f"item[{it.Name}]")
        if (it.Name or "") == "X1":
            x1 = it

    def deep(item, depth):
        global x1
        if depth > 4:
            return
        try:
            nm = item.Name or ""
            if nm == "X1":
                globals()["x1"] = item
            if "CPU" in nm.upper() and cpu_holder[0] is None and depth == 1:
                pass
        except Exception:
            pass
        try:
            for sub in item.DeviceItems:
                deep(sub, depth + 1)
        except Exception:
            pass

    cpu_holder = [None]
    deep(mini, 0)
    x1 = globals().get("x1")
    print("x1:", x1.Name if x1 else None)

    if x1 is not None:
        for info in x1.GetAttributeInfos():
            if any(k in info.Name for k in ("Ip", "Subnet", "Address", "Net", "Port", "Pn")):
                try:
                    v = x1.GetAttribute(info.Name)
                    print(f"  x1.{info.Name} = {v}")
                except Exception as e:
                    print(f"  x1.{info.Name} err {str(e)[:80]}")


if __name__ == "__main__":
    main()
