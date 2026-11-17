"""修复v2（新会话）：X2子网 + OPC UA许可证(反射枚举类型) + 机密数据密码属性大搜索。"""
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

    # --- 许可证属性类型调查 ---
    for info in cpu.GetAttributeInfos():
        if "License" in info.Name:
            t = getattr(info, "NetType", None) or getattr(info, "DataType", None)
            print(f"attr {info.Name}: info-type={t}")
            try:
                print(f"   readable attrs of info: {[p.Name for p in info.GetType().GetProperties()]}")
            except Exception:
                pass

    # 程序集里找 OPC UA License 枚举
    for t in asm_types():
        if t is not None and t.IsEnum and ("License" in t.Name or "OpcUa" in t.Name):
            print("enum:", t.FullName, [f.Name for f in t.GetFields()][:8])

    # --- 尝试设置许可证（多种类型） ---
    tried = []
    for val in (1, 2):
        try:
            cpu.SetAttribute("OpcUaPurchasedLicense", val)
            print("license set OK(int):", val, "->", cpu.GetAttribute("OpcUaPurchasedLicense"))
            tried.append(val)
            break
        except Exception as e:
            print(f"license int {val} fail:", str(e)[:150])
    if not tried:
        for t in asm_types():
            if t is not None and t.IsEnum and "OpcUa" in t.FullName and "License" in t.FullName:
                for f in t.GetFields():
                    if f.Name in ("value__",):
                        continue
                    try:
                        cpu.SetAttribute("OpcUaPurchasedLicense", f.GetValue(None))
                        print("license set OK(enum):", f.Name)
                        tried.append(f.Name)
                        break
                    except Exception as e:
                        print(f"enum {f.Name} fail:", str(e)[:120])
                if tried:
                    break

    # --- 机密数据密码属性搜索（所有 mini 设备项） ---
    hits = []

    def search(item):
        try:
            for info in item.GetAttributeInfos():
                if any(k in info.Name for k in ("Confidential", "PlcCommunicationPassword",
                                                "SecureConfig", "EncryptedConfig")):
                    hits.append((item.Name, info.Name))
        except Exception:
            pass
        try:
            for s in item.DeviceItems:
                search(s)
        except Exception:
            pass

    search(mini)
    print("confidential-ish attrs:", hits)

    # PlcAccessControlConfiguration 枚举值
    try:
        v = cpu.GetAttribute("PlcAccessControlConfiguration")
        print("access control =", v)
        if not isinstance(v, int):
            print("  enum fields:", [f.Name for f in v.GetType().GetFields()])
    except Exception as e:
        print("access control read err:", str(e)[:120])

    # --- X2 子网修复 ---
    NI = find_type("Siemens.Engineering.HW.Features.NetworkInterface")

    def walk(item):
        try:
            if (item.GetAttribute("Label") or "") == "X2":
                svc = item.GetService[NI]()
                for n in svc.Nodes:
                    n.SetAttribute("Address", "192.168.2.1")
                    print("X2 ->", n.GetAttribute("Address"))
        except Exception:
            pass
        try:
            for s in item.DeviceItems:
                walk(s)
        except Exception:
            pass

    walk(mini)
    project.Save()
    print("saved.")


if __name__ == "__main__":
    main()
