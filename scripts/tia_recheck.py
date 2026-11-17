"""打开 NoSafety 副本并编译检查（验证机密数据密码等修复是否保留）。"""
from __future__ import annotations

import sys
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
import os
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402
ASM = clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))
import System  # noqa: E402
from System.IO import FileInfo  # noqa: E402
from Siemens.Engineering import TiaPortal  # noqa: E402

COPY = r"F:\Sicar程序理解\巢湖三拉环线\Line03MCPBoZhonSiemens20260401_NoSafety.ap20\Line03MCPBoZhonSiemens20260401_NoSafety.ap20.ap20"


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


def main() -> None:
    tia = None
    for proc in TiaPortal.GetProcesses():
        try:
            t = proc.Attach()
            if t.Projects.Count > 0:
                tia, project = t, t.Projects[0]
                break
            tia = t
        except Exception:
            continue
    if tia is None:
        raise RuntimeError("no TIA")
    if tia.Projects.Count == 0:
        print("opening copy...")
        project = tia.Projects.Open(FileInfo(COPY))
        print("opened:", project.Path)
    else:
        project = tia.Projects[0]
        print("already open:", project.Path)

    mini = next((d for d in project.Devices
                 if any("MiniPLC" in (i.Name or "") for i in d.DeviceItems)), None)
    cpu = next(i for i in mini.DeviceItems if (i.Name or "") == "MiniPLC")
    ICOMP = find_type("Siemens.Engineering.Compiler.ICompilable")
    res = cpu.GetService[ICOMP]().Compile()
    msgs = []

    def w(m):
        try:
            if m.Description:
                msgs.append((str(m.State), m.Description))
        except Exception:
            pass
        try:
            for s in m.Messages:
                w(s)
        except Exception:
            pass

    for m in res.Messages:
        w(m)
    print(f"=== {len(msgs)} 条 ===")
    for st, d in msgs:
        print(f"  [{st}] {d}")
    project.Save()
    print("saved.")


if __name__ == "__main__":
    main()
