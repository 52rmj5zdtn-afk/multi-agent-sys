"""从主 PLC 导出一个标准全局 DB 作为 XML 结构参照，并检查 OpcUaServer 属性类型。"""
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


def main() -> None:
    tia, project = attach()
    # 找主 PLC (Zone1_PLC1516TF)
    main_dev = None
    for dev in project.Devices:
        for it in dev.DeviceItems:
            if "Zone1_PLC1516TF" in (it.Name or ""):
                main_dev = dev
                break
        if main_dev:
            break
    if main_dev is None:
        raise SystemExit("main plc not found")

    SC = find_type("SoftwareContainer")
    sw_holder = []

    def walk(item):
        if sw_holder:
            return
        try:
            sc = item.GetService[SC]()
            if sc is not None and sc.Software is not None:
                sw_holder.append((item, sc.Software))
                return
        except Exception:
            pass
        try:
            for sub in item.DeviceItems:
                walk(sub)
        except Exception:
            pass

    walk(main_dev)
    _, sw = sw_holder[0]

    from System.IO import FileInfo
    export_opts = find_type("ExportOptions")
    print("ExportOptions:", [f.Name for f in export_opts.GetFields()] if export_opts else None)

    gds = []
    def collect(g):
        for b in g.Blocks:
            if b.GetType().Name == "GlobalDB":
                gds.append((g.Name, b))
        for sub in g.Groups:
            collect(sub)
    collect(sw.BlockGroup)
    print("global DBs:", len(gds))

    out = Path(r"C:\tmp_kimi\ref_db.xml")
    done = False
    for gname, b in gds[:40]:
        try:
            b.Export(FileInfo(str(out)), System.Enum.Parse(export_opts, "None"))
            print("exported:", gname, "/", b.Name)
            done = True
            break
        except Exception as e:
            print("export fail:", b.Name, str(e)[:120])
    if done:
        txt = out.read_text(encoding="utf-8", errors="replace")
        print("=== XML first 4000 chars ===")
        print(txt[:4000])

    # OpcUaServer 属性类型
    opc = []
    def find_opc(item):
        try:
            if "OPC UA" in (item.Name or ""):
                opc.append(item)
        except Exception:
            pass
        try:
            for sub in item.DeviceItems:
                find_opc(sub)
        except Exception:
            pass
    mini = next((d for d in project.Devices if "MiniPLC" in (d.Name or "")), None)
    find_opc(mini)
    for it in opc:
        try:
            v = it.GetAttribute("OpcUaServer")
            print("OpcUaServer =", v, type(v))
        except Exception as e:
            print("OpcUaServer get err:", str(e)[:150])


if __name__ == "__main__":
    main()
