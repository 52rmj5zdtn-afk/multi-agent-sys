"""tia_find_compile.py — 在 Openness 程序集中搜索 Compil 相关类型。"""
from __future__ import annotations

import os
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402

clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))
import System  # noqa: E402

for dll in ["Siemens.Engineering.dll", "Siemens.Engineering.HW.dll", "Siemens.Engineering.SW.dll"]:
    p = API_DIR / dll
    if not p.exists():
        continue
    try:
        asm = System.Reflection.Assembly.LoadFrom(str(p))
    except Exception as e:
        print(f"{dll}: load err {e}")
        continue
    print(f"== {dll} ==")
    try:
        types = asm.GetTypes()
    except System.Reflection.ReflectionTypeLoadException as e:
        types = [x for x in e.Types if x is not None]
    for ty in types:
        if "Compil" in ty.FullName:
            methods = [m.Name for m in ty.GetMethods() if m.DeclaringType == ty]
            print(f"  {ty.FullName}  methods={methods[:8]}")
