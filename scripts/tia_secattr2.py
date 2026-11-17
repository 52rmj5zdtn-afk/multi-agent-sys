"""dump CPU security-related attributes via GetAttributeInfos."""
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
        print("CPU:", nm, "type:", it.GetType().FullName)
        try:
            infos = it.GetAttributeInfos()
            names = [i.Name for i in infos]
            hits = [n for n in names if any(k in n.lower() for k in ("cert", "protect", "secur", "safet", "tls", "firm", "article", "ident"))]
            print("attr infos count:", len(names))
            for n in hits:
                print("  ", n)
        except Exception as e:
            print("GetAttributeInfos err:", str(e)[:200])
        # check known security services
        for svc in ("Siemens.Engineering.HW.Features.SecurityProvider",
                    "Siemens.Engineering.HW.Features.CertificateManager",
                    "Siemens.Engineering.Security.CertificateManager"):
            try:
                ctype = ASM.GetType(svc)
                if ctype is None:
                    continue
                m = it.GetType().GetMethod("GetService")
                s = m.MakeGenericMethod(ctype).Invoke(it, None)
                print("svc", svc, "->", s)
            except Exception:
                pass
