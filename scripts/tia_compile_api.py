"""通过 ICompilable/CompileProvider 编译 MiniPLC 硬件+软件并打印全部错误消息。"""
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


def asm_types():
    try:
        return ASM.GetTypes()
    except Exception as e:
        return e.Types


def find_type(fullname):
    for t in asm_types():
        if t is not None and t.FullName == fullname:
            return t
    return None


def attach():
    for proc in TiaPortal.GetProcesses():
        try:
            tia = proc.Attach()
            if tia.Projects.Count > 0:
                return tia, tia.Projects[0]
        except Exception:
            continue
    raise RuntimeError("no TIA process with open project")


ICOMP = None
for t in asm_types():
    if t is not None and t.FullName == "Siemens.Engineering.Compiler.ICompilable":
        ICOMP = t
print("ICompilable:", ICOMP)


def try_compile(obj, label):
    try:
        svc = obj.GetService[ICOMP]()
    except Exception as e:
        print(f"{label}: no ICompilable ({str(e)[:80]})")
        return
    if svc is None:
        print(f"{label}: ICompilable = None")
        return
    print(f"{label}: compiling via {svc.GetType().FullName} ...")
    try:
        res = svc.Compile()
    except Exception as e:
        print(f"{label}: Compile() threw:", str(e)[:200])
        return
    try:
        msgs = list(res.Messages)
        print(f"{label}: {len(msgs)} messages")
        for m in msgs:
            state = str(m.State)
            if state.upper() in ("ERROR", "WARNING") or "错误" in state:
                print("  !!", state, "|", m.Message)
        if not any(str(m.State).upper() in ("ERROR", "WARNING") for m in msgs):
            for m in msgs[:5]:
                print("   ok:", m.State, "|", m.Message)
    except Exception as e:
        print("msgs err:", str(e)[:120])


def main() -> None:
    tia, project = attach()
    mini = next((d for d in project.Devices
                 if any("MiniPLC" in (i.Name or "") for i in d.DeviceItems)), None)
    try_compile(project, "project")
    try_compile(mini, "device")
    for it in mini.DeviceItems:
        try_compile(it, f"item[{it.Name}]")
        for s2 in it.DeviceItems:
            try_compile(s2, f"  item[{it.Name}/{s2.Name}]")


if __name__ == "__main__":
    main()
