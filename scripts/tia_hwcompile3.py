"""tia_hwcompile3.py — 编译 PLC 站硬件，递归展开全部消息树。"""
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


def dump_msg(m, indent=0):
    pad = "  " * indent
    desc = ""
    try:
        desc = m.Description
    except Exception:
        pass
    path = ""
    try:
        path = m.Path
    except Exception:
        pass
    state = ""
    try:
        state = str(m.State)
    except Exception:
        pass
    print(f"{pad}[{state}] desc='{desc}' path='{path}'")
    try:
        kids = list(m.Messages)
        for k in kids:
            dump_msg(k, indent + 1)
    except Exception:
        pass


def main() -> None:
    tia, project = attach()
    print(f"project: {project.Name}")
    icomp = t("Siemens.Engineering.Compiler.ICompilable")
    for dev in project.Devices:
        comp = gs(dev, "Siemens.Engineering.Compiler.ICompilable")
        if comp is None:
            continue
        res = comp.Compile()
        print(f"== HW {dev.Name}: State={res.State}")
        for m in res.Messages:
            dump_msg(m)


if __name__ == "__main__":
    main()
