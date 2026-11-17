"""续：在副本项目里完善 MiniPLC（设备已建）——递归找带 SoftwareContainer 的 CPU 项，
建 WELD_DB/LINE_DB + 变量，启用 OPC UA，编译保存。"""
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
from Siemens.Engineering import TiaPortal  # noqa: E402


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
            if tia.Projects.Count > 0:
                return tia, tia.Projects[0]
        except Exception:
            continue
    raise RuntimeError("no TIA process with open project")


def show_methods(obj, label, keywords):
    ms = sorted({f"{m.Name}({','.join(p.ParameterType.Name for p in m.GetParameters())})"
                 for m in obj.GetType().GetMethods()
                 if any(k in m.Name for k in keywords)})
    print(f"[methods] {label}: {ms}")


def main() -> None:
    tia, project = attach()
    print("project:", project.Path)

    mini = None
    for dev in project.Devices:
        if "MiniPLC" in (dev.Name or ""):
            mini = dev
    if mini is None:
        raise SystemExit("MiniPLC device missing")
    print("device:", mini.Name)

    SC = find_type("SoftwareContainer")
    holder = []

    def walk(item, depth):
        if holder:
            return
        try:
            sc = item.GetService[SC]()
            if sc is not None and sc.Software is not None:
                holder.append((item, sc.Software))
                print("cpu item:", item.Name, "sw:", sc.Software.GetType().Name)
                return
        except Exception:
            pass
        try:
            for sub in item.DeviceItems:
                walk(sub, depth + 1)
        except Exception:
            pass

    walk(mini, 0)
    if not holder:
        raise SystemExit("no software container under MiniPLC")
    cpu, sw = holder[0]

    blocks = sw.BlockGroup.Blocks
    show_methods(blocks, "Blocks", ("Create",))
    existing = set()
    try:
        existing = {b.Name for b in blocks}
    except Exception:
        pass
    print("existing blocks:", existing)

    def ensure_db(name, members):
        if name in existing:
            print(name, "exists")
            return
        gbd = None
        try:
            gbd = blocks.CreateGlobalDB(name)
            print(name, "created")
        except Exception as e:
            print("CreateGlobalDB fail:", str(e)[:200])
            return
        show_methods(gbd.Interface.Members, f"{name}.Members", ("Create",))
        for nm, ty in members:
            try:
                gbd.Interface.Members.Create(nm, ty)
                print("  member", nm, ty)
            except Exception as e:
                print("  member FAIL:", nm, str(e)[:200])

    ensure_db("WELD_DB", [("weld_current_a", "Real"), ("weld_voltage_v", "Real"),
                          ("weld_power_kw", "Real"), ("resist_dev_pct", "Real"),
                          ("weld_temp_c", "Real"), ("cycle_id", "DInt"), ("alarm", "Bool")])
    ensure_db("LINE_DB", [("line_mode", "Int"), ("opmode_no", "Int"), ("heartbeat", "Bool")])

    # OPC UA
    try:
        opcua = sw.OpcUa
        names = [i.Name for i in opcua.GetAttributeInfos()]
        print("opcua candidate attrs:", [n for n in names if any(k in n.lower() for k in ("enabl", "activ", "server"))])
        for cand in ("ServerEnabled", "ActivateServer", "Enabled"):
            if cand in names:
                opcua.SetAttribute(cand, True)
                print("set", cand, "= True")
    except Exception as e:
        print("opcua fail:", str(e)[:200])

    # 块可访问属性
    for b in sw.BlockGroup.Blocks:
        try:
            infos = {i.Name for i in b.GetAttributeInfos()}
            hits = {n for n in infos if "OPC" in n.upper() or "HMI" in n.upper() or "ACCESSIBLE" in n.upper()}
            for h in hits:
                try:
                    b.SetAttribute(h, True)
                    print(f"{b.Name}.{h} = True")
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
