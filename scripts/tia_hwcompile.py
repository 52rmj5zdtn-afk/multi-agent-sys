"""tia_hwcompile.py — 编译硬件配置并输出全部错误/警告，定位下载失败原因。"""
from __future__ import annotations

import os
import sys
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


def get_service(obj, type_name: str):
    ctype = t(type_name)
    if ctype is None:
        return None
    m = obj.GetType().GetMethod("GetService")
    if m is None:
        return None
    return m.MakeGenericMethod(ctype).Invoke(obj, None)


def attach():
    for proc in TiaPortal.GetProcesses():
        if proc.ProjectPath:
            tia = proc.Attach()
            return tia, tia.Projects[0]
    raise RuntimeError("TIA 无打开项目")


def dump_result(result, label):
    print(f"== {label}: State={result.State} ==")
    msgs = list(result.Messages)
    print(f"消息数: {len(msgs)}")
    n_err = 0
    for m in msgs:
        state = m.State
        desc = m.Description
        path = ""
        try:
            path = m.Path
        except Exception:
            pass
        if "Error" in str(state):
            n_err += 1
        print(f"  [{state}] {desc}  @ {path}")
    print(f"-- {label}: 错误数={n_err}")


def main() -> None:
    tia, project = attach()
    print(f"project: {project.Name}")
    for dev in project.Devices:
        comp = None
        try:
            comp = get_service(dev, "Siemens.Engineering.ICompilable")
        except Exception as e:
            print(f"device {dev.Name}: GetService err {e}")
        if comp is None:
            continue
        print(f"device: {dev.Name}")
        try:
            res = comp.Compile()
            dump_result(res, dev.Name)
        except Exception as e:
            print(f"  compile err: {type(e).__name__}: {str(e)[:300]}")


if __name__ == "__main__":
    main()
