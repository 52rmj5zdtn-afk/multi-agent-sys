"""dump project CPU article/firmware version via Openness."""
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
        name = ""
        try:
            name = it.Name
        except Exception:
            pass
        if "PLC1516" in (name or ""):
            print("item:", name)
            for attr in ("ArticleNumber", "FirmwareVersion", "Version", "TypeIdentifier"):
                try:
                    v = it.GetAttribute(attr)
                    print(f"  {attr} = {v}")
                except Exception as e:
                    print(f"  {attr}: n/a ({type(e).__name__})")
            try:
                ti = it.TypeIdentifier
                print("  TypeIdentifier prop:", ti)
            except Exception:
                pass
