"""修复 MiniPLC 编译错误：
1) OpcUaPurchasedLicense → 有效值  2) X2 改到不同子网  3) 机密数据密码/访问控制
然后重编译验证。"""
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


def main() -> None:
    tia, project = attach()
    mini = next((d for d in project.Devices
                 if any("MiniPLC" in (i.Name or "") for i in d.DeviceItems)), None)
    cpu = next(i for i in mini.DeviceItems if (i.Name or "") == "MiniPLC")

    # 发现相关属性（含可写性线索）
    names = [i.Name for i in cpu.GetAttributeInfos()]
    interesting = [n for n in names if any(k in n for k in
                   ("License", "Password", "Confidential", "AccessControl", "Certificate", "OpcUa"))]
    print("cpu interesting attrs:", interesting)

    # 1) OpcUaPurchasedLicense: 看当前值类型/枚举
    try:
        v = cpu.GetAttribute("OpcUaPurchasedLicense")
        print("OpcUaPurchasedLicense =", v, type(v))
    except Exception as e:
        print("read license err:", str(e)[:120])
    for cand in (1,):
        try:
            cpu.SetAttribute("OpcUaPurchasedLicense", cand)
            print("set OpcUaPurchasedLicense =", cand, "->", cpu.GetAttribute("OpcUaPurchasedLicense"))
            break
        except Exception as e:
            print("set license fail:", str(e)[:200])

    # 3) 访问控制: 找免密码模式
    try:
        v = cpu.GetAttribute("PlcAccessControlConfiguration")
        print("PlcAccessControlConfiguration =", v, type(v))
        fields = [f.Name for f in type(v).GetFields()] if not isinstance(v, int) else None
        print("  fields:", fields)
        for cand_name in ("EnabledWithoutPassword", "Disabled", "EnabledWithoutAccessControl"):
            if fields and cand_name in fields:
                import System
                nv = System.Enum.Parse(type(v), cand_name)
                cpu.SetAttribute("PlcAccessControlConfiguration", nv)
                print("  set ->", cand_name)
                break
    except Exception as e:
        print("access control err:", str(e)[:200])

    # 找机密数据密码属性并设置
    for n in names:
        if "Confidential" in n or ("Password" in n and "Protection" not in n):
            try:
                cur = cpu.GetAttribute(n)
                print(f"{n} = {cur} ({type(cur)})")
            except Exception:
                pass

    # 2) X2 网络节点改 192.168.2.1
    NI = find_type("Siemens.Engineering.HW.Features.NetworkInterface")
    for it in mini.DeviceItems:
        pass

    def walk(item):
        try:
            if (item.GetAttribute("Label") or "") == "X2":
                svc = item.GetService[NI]()
                for n in svc.Nodes:
                    n.SetAttribute("Address", "192.168.2.1")
                    n.SetAttribute("SubnetMask", "255.255.255.0")
                    print("X2 ->", n.GetAttribute("Address"))
        except Exception:
            pass
        try:
            for s in item.DeviceItems:
                walk(s)
        except Exception:
            pass

    walk(mini)

    # 重编译验证
    ICOMP = find_type("Siemens.Engineering.Compiler.ICompilable")
    svc = cpu.GetService[ICOMP]()
    res = svc.Compile()

    def walk_msgs(m, d):
        desc = ""
        try:
            desc = m.Description
        except Exception:
            pass
        st = str(m.State)
        err = getattr(m, "ErrorCount", 0)
        if desc or st != "None":
            print("  " * d + f"[{st} e={err}] {desc}")
        try:
            for sub in m.Messages:
                walk_msgs(sub, d + 1)
        except Exception:
            pass

    for m in res.Messages:
        walk_msgs(m, 1)

    project.Save()
    print("saved.")


if __name__ == "__main__":
    main()
