"""MiniPLC(5)：删除后按完整格式重导 DB（OPC UA 可访问），启用 OpcUaServer。"""
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

MEMBER = """    <Member Name="{n}" Datatype="{t}">
      <AttributeList>
        <BooleanAttribute Name="ExternalAccessible" SystemDefined="true">true</BooleanAttribute>
        <BooleanAttribute Name="ExternalVisible" SystemDefined="true">true</BooleanAttribute>
        <BooleanAttribute Name="ExternalWritable" SystemDefined="true">true</BooleanAttribute>
      </AttributeList>
    </Member>"""

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
      <DBAccessibleFromOPCUA>true</DBAccessibleFromOPCUA>
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

    # 删除旧 DB（标准块，无密码问题）
    for b in list(blocks):
        nm = "(unknown)"
        try:
            nm = b.Name
        except Exception:
            pass
        if nm in ("WELD_DB", "LINE_DB"):
            try:
                b.Delete()
                print("deleted old", nm)
            except Exception as e:
                print("delete fail:", nm, str(e)[:150])

    from System.IO import FileInfo
    import_io = find_type("ImportOptions")
    opts = System.Enum.Parse(import_io, "None")
    for name, number, members in DBS:
        mem_xml = "\n".join(MEMBER.format(n=n, t=t) for n, t in members)
        xml = TEMPLATE.format(name=name, number=number, members=mem_xml)
        p = WORK / f"{name}.xml"
        p.write_text(xml, encoding="utf-8")
        try:
            b = blocks.Import(FileInfo(str(p)), opts)
            print(name, "imported OK")
            try:
                print("  DBAccessibleFromOPCUA =", b.GetAttribute("DBAccessibleFromOPCUA"))
            except Exception as e:
                print("  read attr err:", str(e)[:100])
        except Exception as e:
            print(name, "import FAIL:", str(e)[:400])

    # OpcUaServer = True
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
            it.SetAttribute("OpcUaServer", True)
            print("OpcUaServer set ->", it.GetAttribute("OpcUaServer"))
        except Exception as e:
            print("set OpcUaServer fail:", str(e)[:200])

    project.Save()
    print("saved.")


if __name__ == "__main__":
    main()
