"""tia_hwcompile2.py — 深度诊断：列出设备/编译服务并编译，输出全部消息。"""
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
    ty = ASM.GetType(name)
    print(f"type {name} -> {ty}")
    return ty


def attach():
    for proc in TiaPortal.GetProcesses():
        if proc.ProjectPath:
            tia = proc.Attach()
            return tia, tia.Projects[0]
    raise RuntimeError("TIA 无打开项目")


def dump_result(result, label):
    msgs = list(result.Messages)
    n_err = 0
    for m in msgs:
        state = str(m.State)
        if "Error" in state:
            n_err += 1
    print(f"== {label}: State={result.State} 消息={len(msgs)} 错误={n_err} ==")
    for m in msgs:
        path = ""
        try:
            path = m.Path
        except Exception:
            pass
        print(f"  [{m.State}] {m.Description} @ {path}")
    return n_err


def main() -> None:
    tia, project = attach()
    print(f"project: {project.Name}")

    icomp = t("Siemens.Engineering.Compiler.ICompilable")
    print(f"devices: {len(list(project.Devices))}")
    for dev in project.Devices:
        print(f"device: {dev.Name} type={dev.GetType().FullName}")
        # try GetService<ICompilable>
        try:
            m = dev.GetType().GetMethod("GetService")
            svc = m.MakeGenericMethod(icomp).Invoke(dev, None)
            print(f"  ICompilable service: {svc}")
            if svc is not None:
                res = svc.Compile()
                dump_result(res, f"HW {dev.Name}")
        except Exception as e:
            print(f"  compile err: {type(e).__name__}: {str(e)[:250]}")
        # PLC software compile
        stack = list(dev.DeviceItems)
        while stack:
            it = stack.pop()
            try:
                stack.extend(list(it.DeviceItems))
            except Exception:
                pass
            try:
                m = it.GetType().GetMethod("GetService")
                hw_sw = m.MakeGenericMethod(
                    t("Siemens.Engineering.HW.Features.SoftwareContainer")).Invoke(it, None)
                if hw_sw is not None and hw_sw.Software is not None:
                    sw = hw_sw.Software
                    svc = m.MakeGenericMethod(icomp).Invoke(sw, None)
                    if svc is not None:
                        res = svc.Compile()
                        dump_result(res, f"SW {dev.Name}")
            except Exception:
                pass


if __name__ == "__main__":
    main()
