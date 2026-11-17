"""保底方案：Openness 创建最小可下载项目（F-CPU 1516TF、无安全程序 → 免密码下载到现有虚拟PLC）。

步骤：关闭当前项目 → Projects.Create → CreateWithItem(1516TF V4.0) → 建 DB+变量
→ 启用 OPC UA → 编译 → 保存。每步先反射可用 API 再调用。
用法: python scripts/tia_minimal_project.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
import os
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402
ASM = clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))
import System  # noqa: E402
from Siemens.Engineering import TiaPortal  # noqa: E402

CPU_TYPE = "OrderNumber:6ES7 516-3UP10-0AB0/V4.0"   # 与 Line03PLC 虚拟实例同型号


def asm_types():
    try:
        return ASM.GetTypes()
    except Exception as e:
        return e.Types


def find_type(name):
    for t in asm_types():
        if t is not None and t.Name == name:
            return t
    return None


def attach():
    for proc in TiaPortal.GetProcesses():
        try:
            tia = proc.Attach()
            return tia, (tia.Projects[0] if tia.Projects.Count > 0 else None)
        except Exception:
            continue
    raise RuntimeError("no TIA process")


def show_methods(obj, label, keywords):
    ms = sorted({f"{m.Name}({','.join(p.ParameterType.Name for p in m.GetParameters())})"
                 for m in obj.GetType().GetMethods()
                 if any(k in m.Name for k in keywords)})
    print(f"[methods] {label}: {ms}")


def main() -> None:
    tia, project = attach()
    print("current project:", project.Path if project else None)

    # ---- 1. 关闭当前项目（已保存的副本，原项目不受影响）----
    if project is not None:
        project.Close()
        time.sleep(2)
        print("closed previous project")

    # ---- 2. 创建新项目 ----
    pci_type = find_type("ProjectCreationInfo")
    print("ProjectCreationInfo found:", pci_type)
    pci = System.Activator.CreateInstance(pci_type)
    pci.Name = "MiniLine03"
    from System.IO import DirectoryInfo
    pci.Path = DirectoryInfo(r"F:\Sicar程序理解\巢湖三拉环线\MiniLine03")
    for prop in ("Author", "Comment", "TargetFramework"):
        try:
            print("  pci prop", prop, "=", getattr(pci, prop))
        except Exception:
            pass
    show_methods(tia.Projects, "Projects", ("Create",))
    project = tia.Projects.Create(pci)
    print("created:", project.Path)

    # ---- 3. 添加 CPU ----
    show_methods(project.Devices, "Devices", ("Create",))
    dev = None
    err = None
    try:
        dev = project.Devices.CreateWithItem(CPU_TYPE, "0", "MiniPLC")
    except Exception as e:
        err = e
        print("CreateWithItem fail:", str(e)[:300])
    if dev is None:
        # 试其它签名
        for name in ("CreateWithArticle", "CreateS71500", "CreateWithItem"):
            m = project.Devices.GetType().GetMethod(name)
            if m:
                print("trying", name)
                try:
                    dev = m.Invoke(project.Devices, [CPU_TYPE, "0", "MiniPLC"])
                    break
                except Exception as e2:
                    print("  fail:", str(e2)[:200])
    if dev is None:
        raise SystemExit("cannot create device")
    print("device:", dev.Name)

    cpu = None
    for it in dev.DeviceItems:
        if "MiniPLC" in (it.Name or ""):
            cpu = it
    print("cpu item:", cpu.Name if cpu else None)

    SC = find_type("SoftwareContainer")
    sw = cpu.GetService[SC]().Software
    print("software:", sw.GetType().Name)

    # ---- 4. 创建 DB ----
    blocks = sw.BlockGroup.Blocks
    show_methods(blocks, "Blocks", ("Create", "Import"))
    gbd = None
    try:
        gbd = blocks.CreateGlobalDB("WELD_DB")
        print("WELD_DB created")
    except Exception as e:
        print("CreateGlobalDB fail:", str(e)[:300])
    if gbd is None:
        raise SystemExit("cannot create DB")

    # ---- 5. 添加变量 ----
    show_methods(gbd.Interface.Members, "Members", ("Create", "Add"))
    weld_members = [
        ("weld_current_a", "Real"), ("weld_voltage_v", "Real"),
        ("weld_power_kw", "Real"), ("resist_dev_pct", "Real"),
        ("weld_temp_c", "Real"), ("cycle_id", "DInt"), ("alarm", "Bool"),
    ]
    for nm, ty in weld_members:
        try:
            gbd.Interface.Members.Create(nm, ty)
            print("  member", nm, ty)
        except Exception as e:
            print("  member fail:", nm, str(e)[:200])

    gbd2 = None
    try:
        gbd2 = blocks.CreateGlobalDB("LINE_DB")
        for nm, ty in [("line_mode", "Int"), ("opmode_no", "Int"), ("heartbeat", "Bool")]:
            gbd2.Interface.Members.Create(nm, ty)
        print("LINE_DB created with members")
    except Exception as e:
        print("LINE_DB fail:", str(e)[:300])

    # ---- 6. OPC UA 启用 ----
    try:
        opcua = sw.OpcUa
        names = [i.Name for i in opcua.GetAttributeInfos()]
        print("opcua attrs:", [n for n in names if "nabl" in n or "ctivat" in n or "erver" in n])
        for cand in ("ServerEnabled", "ActivateServer", "Enabled"):
            if cand in names:
                opcua.SetAttribute(cand, True)
                print("set", cand, "= True")
    except Exception as e:
        print("opcua enable fail:", str(e)[:200])

    # ---- 7. 编译 + 保存 ----
    res = sw.Compile()
    try:
        errs = [m for m in res.Messages if str(m.State).upper() == "ERROR"]
        print("compile errors:", len(errs))
        for m in errs[:10]:
            print("  ", m.Message)
    except Exception as e:
        print("compile msgs err:", str(e)[:200])
    project.Save()
    print("saved:", project.Path)


if __name__ == "__main__":
    main()
