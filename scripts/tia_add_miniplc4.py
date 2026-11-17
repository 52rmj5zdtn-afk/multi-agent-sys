"""MiniPLC(4)：按参照格式 XML 导入 WELD_DB/LINE_DB，设 OPC UA 可访问，启用服务器。"""
from __future__ import annotations

import sys
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
WORK = Path(r"C:\tmp_kimi")
import os
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402
ASM = clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))
import System  # noqa: E402
from Siemens.Engineering import TiaPortal  # noqa: E402

TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<Document>
  <Engineering version="V20" />
  <SW.Blocks.GlobalDB ID="0">
    <AttributeList>
      <AutoNumber>false</AutoNumber>
      <Interface><Sections xmlns="http://www.siemens.com/automation/Openness/SW/Interface/v5">
  <Section Name="Static">
{members}
  </Section>
</Sections></Interface>
      <Name>{name}</Name>
      <Namespace />
      <Number>{number}</Number>
      <ProgrammingLanguage>DB</ProgrammingLanguage>
      <MemoryLayout>Optimized</MemoryLayout>
      <MemoryReserve>100</MemoryReserve>
    </AttributeList>
  </SW.Blocks.GlobalDB>
</Document>
"""

DBS = [
    ("WELD_DB", 10, [
        ("weld_current_a", "Real"), ("weld_voltage_v", "Real"),
        ("weld_power_kw", "Real"), ("resist_dev_pct", "Real"),
        ("weld_temp_c", "Real"), ("cycle_id", "DInt"), ("alarm", "Bool"),
    ]),
    ("LINE_DB", 11, [
        ("line_mode", "Int"), ("opmode_no", "Int"), ("heartbeat", "Bool"),
    ]),
]


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
    mini = next((d for d in project.Devices if "MiniPLC" in (d.Name or "")), None)
    if mini is None:
        raise SystemExit("MiniPLC missing")

    SC = find_type("SoftwareContainer")
    holder = []

    def walk(item):
        if holder:
            return
        try:
            sc = item.GetService[SC]()
            if sc is not None and sc.Software is not None:
                holder.append((item, sc.Software))
                return
        except Exception:
            pass
        try:
            for sub in item.DeviceItems:
                walk(sub)
        except Exception:
            pass

    walk(mini)
    cpu_item, sw = holder[0]
    blocks = sw.BlockGroup.Blocks
    existing = {b.Name for b in blocks}

    from System.IO import FileInfo
    import_io = find_type("ImportOptions")
    opts = System.Enum.Parse(import_io, "None")

    for name, number, members in DBS:
        if name in existing:
            print(name, "exists, skip import")
            continue
        mem_xml = "\n".join(f'    <Member Name="{n}" Datatype="{t}" />' for n, t in members)
        xml = TEMPLATE.format(name=name, number=number, members=mem_xml)
        p = WORK / f"{name}.xml"
        p.write_text(xml, encoding="utf-8")
        try:
            b = blocks.Import(FileInfo(str(p)), opts)
            print(name, "imported OK")
        except Exception as e:
            print(name, "import FAIL:", str(e)[:300])

    # 设 DBAccessibleFromOPCUA / FromHMI
    for b in blocks:
        if b.Name in ("WELD_DB", "LINE_DB"):
            for attr in ("DBAccessibleFromOPCUA", "DBAccessibleFromHMI"):
                try:
                    infos = [i.Name for i in b.GetAttributeInfos()]
                    if attr in infos:
                        b.SetAttribute(attr, True)
                        print(f"{b.Name}.{attr}=True")
                    else:
                        print(f"{b.Name}: no attr {attr}; attrs sample:",
                              [n for n in infos if "ccess" in n or "HMI" in n or "OPC" in n])
                except Exception as e:
                    print(f"{b.Name}.{attr} fail:", str(e)[:120])

    # 启用 OPC UA 服务器
    opc_items = []

    def find_opc(item):
        try:
            if "OPC UA" in (item.Name or ""):
                opc_items.append(item)
        except Exception:
            pass
        try:
            for sub in item.DeviceItems:
                find_opc(sub)
        except Exception:
            pass

    find_opc(mini)
    for it in opc_items:
        try:
            v = it.GetAttribute("OpcUaServer")
            print("OpcUaServer current:", v, "| type:", v.GetType().Name if v else None)
            if v is not None and v.GetType().Name == "Boolean":
                it.SetAttribute("OpcUaServer", True)
                print("OpcUaServer = True")
        except Exception as e:
            print("OpcUaServer err:", str(e)[:200])
        # 保险: 再列一次全部属性名
        print("opc item attrs:", [i.Name for i in it.GetAttributeInfos()])

    project.Save()
    print("saved.")


if __name__ == "__main__":
    main()
