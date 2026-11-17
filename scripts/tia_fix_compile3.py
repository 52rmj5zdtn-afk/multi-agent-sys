"""修复v3（新会话，先查 AccessMode 再设置，防会话中毒）：
PlcAccessControlConfiguration → 免密码模式；OpcUaPurchasedLicense → Small；X2 子网；重编译验证。"""
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
from Siemens.Engineering import TiaPortal  # noqa: E402

import Siemens.Engineering.HW as HW  # noqa: E402,F401


def attach():
    for proc in TiaPortal.GetProcesses():
        try:
            tia = proc.Attach()
            if tia.Projects.Count > 0:
                return tia, tia.Projects[0]
        except Exception:
            continue
    raise RuntimeError("no TIA process with open project")


def access_mode(item, attr):
    for info in item.GetAttributeInfos():
        if info.Name == attr:
            return str(info.AccessMode)
    return "?"


def set_attr(item, attr, val):
    am = access_mode(item, attr)
    print(f"  {attr}: AccessMode={am}")
    if "Write" not in am:
        print(f"  !! {attr} 只读，跳过")
        return False
    try:
        item.SetAttribute(attr, val)
        print(f"  set {attr} OK ->", item.GetAttribute(attr))
        return True
    except Exception as e:
        print(f"  set {attr} FAIL:", str(e)[:150])
        return False


def main() -> None:
    tia, project = attach()
    mini = next((d for d in project.Devices
                 if any("MiniPLC" in (i.Name or "") for i in d.DeviceItems)), None)
    cpu = next(i for i in mini.DeviceItems if (i.Name or "") == "MiniPLC")

    # 1) 访问控制：找免密码枚举值
    from Siemens.Engineering.HW import OpcUaPurchasedLicense  # noqa: E402
    cur = cpu.GetAttribute("PlcAccessControlConfiguration")
    print("PlcAccessControlConfiguration =", cur, "| type:", cur.GetType().FullName)
    fields = [f.Name for f in cur.GetType().GetFields() if f.Name != "value__"]
    print("  fields:", fields)
    pick = next((f for f in fields if "Without" in f or f == "Disabled"), None)
    if pick:
        nv = System.Enum.Parse(cur.GetType(), pick)
        set_attr(cpu, "PlcAccessControlConfiguration", nv)

    # 2) OPC UA 许可证
    set_attr(cpu, "OpcUaPurchasedLicense", OpcUaPurchasedLicense.SimaticOpcUaS71500Small)

    # 3) X2 子网
    NI = None
    try:
        types = ASM.GetTypes()
    except Exception as e:
        types = e.Types
    for t in types:
        if t is not None and t.FullName == "Siemens.Engineering.HW.Features.NetworkInterface":
            NI = t

    def walk(item):
        try:
            if (item.GetAttribute("Label") or "") == "X2":
                svc = item.GetService[NI]()
                for n in svc.Nodes:
                    n.SetAttribute("Address", "192.168.2.1")
                    print("  X2 ->", n.GetAttribute("Address"))
        except Exception:
            pass
        try:
            for s in item.DeviceItems:
                walk(s)
        except Exception:
            pass

    walk(mini)

    # 4) 重编译
    ICOMP = None
    for t in types:
        if t is not None and t.FullName == "Siemens.Engineering.Compiler.ICompilable":
            ICOMP = t
    svc = cpu.GetService[ICOMP]()
    res = svc.Compile()
    errors = []

    def walk_msgs(m):
        try:
            d = m.Description
            if d:
                errors.append((str(m.State), d))
        except Exception:
            pass
        try:
            for sub in m.Messages:
                walk_msgs(sub)
        except Exception:
            pass

    for m in res.Messages:
        walk_msgs(m)
    print(f"=== 编译消息 {len(errors)} 条 ===")
    for st, d in errors:
        print(f"  [{st}] {d}")

    project.Save()
    print("saved.")


if __name__ == "__main__":
    main()
