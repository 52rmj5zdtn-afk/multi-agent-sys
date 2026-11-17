"""在 NoSafety 副本中新增无安全程序的 1516TF 设备 MiniPLC + 代表性 DB + OPC UA。

副本项目当前已关闭，先 Projects.Open 再操作。编译只针对 MiniPLC 的软件。
用法: python scripts/tia_add_miniplc.py
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

COPY_PROJECT = r"F:\Sicar程序理解\巢湖三拉环线\Line03MCPBoZhonSiemens20260401_NoSafety.ap20\Line03MCPBoZhonSiemens20260401_NoSafety.ap20.ap20"
CPU_TYPE = "OrderNumber:6ES7 516-3UP10-0AB0/V4.0"


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
    if project is None:
        from System.IO import FileInfo
        show_methods(tia.Projects, "Projects", ("Open", "Create"))
        try:
            project = tia.Projects.Open(FileInfo(COPY_PROJECT))
        except Exception as e:
            print("Open(FileInfo) fail:", str(e)[:200])
            project = tia.Projects.Open(COPY_PROJECT)
        print("opened:", project.Path)
    else:
        print("already open:", project.Path)

    # 若 MiniPLC 已存在则复用
    mini = None
    for dev in project.Devices:
        if "MiniPLC" in (dev.Name or ""):
            mini = dev
            print("MiniPLC exists")
    if mini is None:
        show_methods(project.Devices, "Devices", ("Create",))
        mini = project.Devices.CreateWithItem(CPU_TYPE, "0", "MiniPLC")
        print("device created:", mini.Name)

    cpu = None
    for it in mini.DeviceItems:
        if "MiniPLC" in (it.Name or ""):
            cpu = it
    SC = find_type("SoftwareContainer")
    sw = cpu.GetService[SC]().Software
    print("software:", sw.GetType().Name)

    blocks = sw.BlockGroup.Blocks
    show_methods(blocks, "Blocks", ("Create",))

    existing = {b.Name for b in blocks}
    if "WELD_DB" not in existing:
        gbd = blocks.CreateGlobalDB("WELD_DB")
        show_methods(gbd.Interface.Members, "Members", ("Create",))
        for nm, ty in [("weld_current_a", "Real"), ("weld_voltage_v", "Real"),
                       ("weld_power_kw", "Real"), ("resist_dev_pct", "Real"),
                       ("weld_temp_c", "Real"), ("cycle_id", "DInt"), ("alarm", "Bool")]:
            try:
                gbd.Interface.Members.Create(nm, ty)
                print("  WELD_DB member", nm)
            except Exception as e:
                print("  member fail:", nm, str(e)[:150])
    else:
        print("WELD_DB exists")

    if "LINE_DB" not in existing:
        gbd2 = blocks.CreateGlobalDB("LINE_DB")
        for nm, ty in [("line_mode", "Int"), ("opmode_no", "Int"), ("heartbeat", "Bool")]:
            try:
                gbd2.Interface.Members.Create(nm, ty)
                print("  LINE_DB member", nm)
            except Exception as e:
                print("  member fail:", nm, str(e)[:150])
    else:
        print("LINE_DB exists")

    # OPC UA 启用
    try:
        opcua = sw.OpcUa
        names = [i.Name for i in opcua.GetAttributeInfos()]
        onames = [n for n in names if "nabl" in n or "ctivat" in n.lower() or "erver" in n]
        print("opcua attrs:", onames)
        done = False
        for cand in ("ServerEnabled", "ActivateServer", "Enabled"):
            if cand in names:
                opcua.SetAttribute(cand, True)
                print("set", cand, "= True")
                done = True
        if not done:
            print("available:", names)
    except Exception as e:
        print("opcua fail:", str(e)[:200])

    # DB 可被 OPC UA/HMI 访问（属性在块上）
    for b in sw.BlockGroup.Blocks:
        try:
            infos = [i.Name for i in b.GetAttributeInfos()]
            hits = [n for n in infos if "HMI" in n.upper() or "OPC" in n.upper() or "ACCESSIBLE" in n.upper()]
            if hits:
                print(b.Name, "attrs:", hits)
                for h in hits:
                    if "Access" in h or "Accessible" in h:
                        try:
                            b.SetAttribute(h, True)
                            print("  set", h, "= True")
                        except Exception:
                            pass
        except Exception:
            pass

    res = sw.Compile()
    try:
        errs = [m for m in res.Messages if str(m.State).upper() == "ERROR"]
        warns = [m for m in res.Messages if str(m.State).upper() == "WARNING"]
        print(f"compile: errors={len(errs)} warnings={len(warns)}")
        for m in errs[:10]:
            print("  ERR", m.Message)
    except Exception as e:
        print("msgs err:", str(e)[:150])
    project.Save()
    print("saved.")


if __name__ == "__main__":
    main()
