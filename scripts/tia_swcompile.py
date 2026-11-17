"""tia_swcompile.py — 编译主 PLC 的 PlcSoftware，输出全部编译消息。"""
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
    print(f"project: {project.Name}")
    icomp = t("Siemens.Engineering.Compiler.ICompilable")
    for dev in project.Devices:
        stack = list(dev.DeviceItems)
        while stack:
            it = stack.pop()
            try:
                stack.extend(list(it.DeviceItems))
            except Exception:
                pass
            svc = gs(it, "Siemens.Engineering.HW.Features.SoftwareContainer")
            if svc is None or svc.Software is None:
                continue
            sw = svc.Software
            comp = gs(sw, "Siemens.Engineering.Compiler.ICompilable")
            print(f"sw on {it.Name}: type={sw.GetType().FullName} compilable={comp is not None}")
            if comp is None:
                continue
            res = comp.Compile()
            msgs = list(res.Messages)
            errs = [m for m in msgs if "Error" in str(m.State)]
            print(f"== SW {dev.Name}/{it.Name}: State={res.State} total={len(msgs)} errors={len(errs)}")
            for m in msgs:
                path = ""
                try:
                    path = m.Path
                except Exception:
                    pass
                print(f"  [{m.State}] {m.Description} @ {path}")


if __name__ == "__main__":
    main()
