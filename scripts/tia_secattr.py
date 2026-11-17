"""dump CPU protection/security related attributes and services."""
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


def t(name):
    return ASM.GetType(name)


def gs(obj, tn):
    ctype = t(tn)
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
    raise RuntimeError("no project")


tia, project = attach()
for dev in project.Devices:
    stack = list(dev.DeviceItems)
    while stack:
        it = stack.pop()
        try:
            stack.extend(list(it.DeviceItems))
        except Exception:
            pass
        try:
            nm = it.Name
        except Exception:
            nm = ""
        if "PLC1516" not in (nm or ""):
            continue
        print("CPU:", nm)
        for attr in ("ProtectionLevel", "SafetyMode", "CertificateManagerEnabled",
                     "SecuritySettings", "ProtectionAndSecurity"):
            try:
                v = it.GetAttribute(attr)
                print(f"  attr {attr} = {v}")
            except Exception:
                pass
        # enumerate device item attributes to find security-ish ones
        try:
            attrs = it.GetAttributes()
            names = [a.Name for a in attrs]
            sec = [n for n in names if any(k in n.lower() for k in ("cert", "protect", "secur", "safet", "tls", "firmware", "article"))]
            print("  security-ish attrs:", sec[:30])
        except Exception as e:
            print("  GetAttributes err:", e)
