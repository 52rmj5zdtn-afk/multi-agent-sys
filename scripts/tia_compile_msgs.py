"""读编译消息：反射 CompilerResultMessage 属性并打印全部消息。"""
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


ICOMP = find_type("Siemens.Engineering.Compiler.ICompilable")


def show(obj, label):
    try:
        svc = obj.GetService[ICOMP]()
    except Exception:
        print(f"{label}: no service")
        return
    if svc is None:
        print(f"{label}: none")
        return
    res = svc.Compile()
    msgs = list(res.Messages)
    print(f"--- {label}: {len(msgs)} top messages ---")

    def walk(m, depth):
        parts = []
        for prop in m.GetType().GetProperties():
            if prop.Name in ("Messages", "Parent"):
                continue
            try:
                v = prop.GetValue(m, None)
                if v is not None and v != "":
                    parts.append(f"{prop.Name}={v}")
            except Exception:
                pass
        print("  " * depth + " | ".join(parts))
        try:
            for sub in m.Messages:
                walk(sub, depth + 1)
        except Exception:
            pass

    for m in msgs:
        walk(m, 1)


def main() -> None:
    tia, project = attach()
    mini = next((d for d in project.Devices
                 if any("MiniPLC" in (i.Name or "") for i in d.DeviceItems)), None)
    show(mini, "device")
    for it in mini.DeviceItems:
        if (it.Name or "") == "MiniPLC":
            show(it, "cpu")


if __name__ == "__main__":
    main()
