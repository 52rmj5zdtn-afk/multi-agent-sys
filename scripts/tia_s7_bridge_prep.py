"""最终落地路径：MiniPLC 关闭 OPC UA 服务器 + 启用 PUT/GET + DB 改非优化块，
使 S7 协议(snap7)可读 → 全脚本化下载与采集。编译应清零错误。
"""
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
      <MemoryLayout>Standard</MemoryLayout>
    </AttributeList>
  </SW.Blocks.GlobalDB>
</Document>
"""

DBS = [
    ("WELD_DB", 10, [("weld_current_a", "Real"), ("weld_voltage_v", "Real"),
                     ("weld_power_kw", "Real"), ("resist_dev_pct", "Real"),
                     ("weld_temp_c", "Real"), ("cycle_id", "DInt"), ("alarm", "Bool")]),
    ("LINE_DB", 11, [("line_mode", "Int"), ("opmode_no", "Int"), ("heartbeat", "Bool")]),
]


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


def main() -> None:
    project = None
    for proc in TiaPortal.GetProcesses():
        try:
            t = proc.Attach()
            if t.Projects.Count > 0:
                project = t.Projects[0]
                break
        except Exception:
            continue
    if project is None:
        raise SystemExit("no open project")
    print("project:", str(project.Path), flush=True)

    mini = next((d for d in project.Devices
                 if any("MiniPLC" in (i.Name or "") for i in d.DeviceItems)), None)
    cpu = next(i for i in mini.DeviceItems if (i.Name or "") == "MiniPLC")

    # 1) OpcUaServer=False
    opc_items = []

    def walk_opc(item):
        try:
            if "OPC UA" in (item.Name or ""):
                opc_items.append(item)
        except Exception:
            pass
        try:
            for s in item.DeviceItems:
                walk_opc(s)
        except Exception:
            pass

    walk_opc(mini)
    for it in opc_items:
        it.SetAttribute("OpcUaServer", False)
        print("OpcUaServer=False on", it.Name, flush=True)

    # 2) PUT/GET 允许 + 访问控制关闭（免机密数据密码）
    cpu.SetAttribute("ProtectionEnablePutGetCommunication", True)
    print("PUT/GET enabled:", cpu.GetAttribute("ProtectionEnablePutGetCommunication"), flush=True)
    cur = cpu.GetAttribute("PlcAccessControlConfiguration")
    fields = [f.Name for f in cur.GetType().GetFields() if f.Name != "value__"]
    pick = next((f for f in fields if f == "Disabled"), None)
    if pick:
        nv = System.Enum.Parse(cur.GetType(), pick)
        cpu.SetAttribute("PlcAccessControlConfiguration", nv)
        print("AccessControl ->", cpu.GetAttribute("PlcAccessControlConfiguration"), flush=True)

    # 3) DB 改标准（非优化）布局：删除重导
    SC = find_type("Siemens.Engineering.HW.Features.SoftwareContainer")
    sw = cpu.GetService[SC]().Software
    blocks = sw.BlockGroup.Blocks
    doomed = []
    for b in list(blocks):
        nm = "(?)"
        try:
            nm = b.Name
        except Exception:
            pass
        if nm in ("WELD_DB", "LINE_DB"):
            doomed.append((nm, b))
    for nm, b in doomed:
        try:
            b.Delete()
            print("deleted old", nm, flush=True)
        except Exception as e:
            print("delete fail:", nm, str(e)[:120], flush=True)

    from System.IO import FileInfo
    import_io = None
    for t in asm_types():
        if t is not None and t.Name == "ImportOptions":
            import_io = t
            break
    opts = System.Enum.Parse(import_io, "None")
    for name, number, members in DBS:
        mem_xml = "\n".join(MEMBER.format(n=n, t=t) for n, t in members)
        (WORK / f"{name}.xml").write_text(
            TEMPLATE.format(name=name, number=number, members=mem_xml), encoding="utf-8")
        try:
            blocks.Import(FileInfo(str(WORK / f"{name}.xml")), opts)
            print(name, "reimported (Standard layout)", flush=True)
        except Exception as e:
            print(name, "import FAIL:", str(e)[:250], flush=True)

    # 4) 编译检查
    ICOMP = find_type("Siemens.Engineering.Compiler.ICompilable")
    res = cpu.GetService[ICOMP]().Compile()
    msgs = []

    def w(m):
        try:
            if m.Description:
                msgs.append((str(m.State), m.Description))
        except Exception:
            pass
        try:
            for s in m.Messages:
                w(s)
        except Exception:
            pass

    for m in res.Messages:
        w(m)
    errs = [(s, d) for s, d in msgs if s == "Error"]
    print(f"=== {len(msgs)} msgs, {len(errs)} errors ===", flush=True)
    for st, d in msgs:
        print(f"  [{st}] {d}", flush=True)
    project.Save()
    print("saved.", flush=True)


if __name__ == "__main__":
    main()
