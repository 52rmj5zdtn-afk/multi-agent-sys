"""MiniPLC(3)：XML 导入 WELD_DB/LINE_DB；OPC UA_1 子项启用；尽力编译；保存。"""
from __future__ import annotations

import sys
import time
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

WELD_XML = """<?xml version="1.0" encoding="utf-8"?>
<Document xmlns="http://www.siemens.com/automation/Openness/SW/PlcBlock/Xml">
  <Engineering version="V20" />
  <SW.Blocks.GlobalDB ID="0">
    <AttributeList>
      <Name>WELD_DB</Name>
      <Number>10</Number>
      <ProgrammingLanguage>DB</ProgrammingLanguage>
      <DBAccessibleFromOPCUA>true</DBAccessibleFromOPCUA>
      <DBAccessibleFromHMI>true</DBAccessibleFromHMI>
    </AttributeList>
    <ObjectList>
      <SW.InterfaceSection ID="1" CompositionName="Interface">
        <AttributeList>
          <Sections xmlns="http://www.siemens.com/automation/Openness/SW/Interface/Section">
            <Section Name="Static">
              <Member Name="weld_current_a" Datatype="Real" />
              <Member Name="weld_voltage_v" Datatype="Real" />
              <Member Name="weld_power_kw" Datatype="Real" />
              <Member Name="resist_dev_pct" Datatype="Real" />
              <Member Name="weld_temp_c" Datatype="Real" />
              <Member Name="cycle_id" Datatype="DInt" />
              <Member Name="alarm" Datatype="Bool" />
            </Section>
          </Sections>
        </AttributeList>
      </SW.InterfaceSection>
    </ObjectList>
  </SW.Blocks.GlobalDB>
</Document>
"""

LINE_XML = """<?xml version="1.0" encoding="utf-8"?>
<Document xmlns="http://www.siemens.com/automation/Openness/SW/PlcBlock/Xml">
  <Engineering version="V20" />
  <SW.Blocks.GlobalDB ID="0">
    <AttributeList>
      <Name>LINE_DB</Name>
      <Number>11</Number>
      <ProgrammingLanguage>DB</ProgrammingLanguage>
      <DBAccessibleFromOPCUA>true</DBAccessibleFromOPCUA>
      <DBAccessibleFromHMI>true</DBAccessibleFromHMI>
    </AttributeList>
    <ObjectList>
      <SW.InterfaceSection ID="1" CompositionName="Interface">
        <AttributeList>
          <Sections xmlns="http://www.siemens.com/automation/Openness/SW/Interface/Section">
            <Section Name="Static">
              <Member Name="line_mode" Datatype="Int" />
              <Member Name="opmode_no" Datatype="Int" />
              <Member Name="heartbeat" Datatype="Bool" />
            </Section>
          </Sections>
        </AttributeList>
      </SW.InterfaceSection>
    </ObjectList>
  </SW.Blocks.GlobalDB>
</Document>
"""


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
    print("cpu item:", cpu_item.Name, "| sw:", sw.GetType().Name)

    blocks = sw.BlockGroup.Blocks
    show_methods(blocks, "Blocks", ("Import", "Create"))
    existing = {b.Name for b in blocks}
    print("existing:", existing)

    from System.IO import FileInfo
    import_io = find_type("ImportOptions")
    print("ImportOptions fields:", [f.Name for f in import_io.GetFields()] if import_io else None)

    for name, xml in (("WELD_DB", WELD_XML), ("LINE_DB", LINE_XML)):
        if name in existing:
            print(name, "exists, skip")
            continue
        p = WORK / f"{name}.xml"
        p.write_text(xml, encoding="utf-8")
        try:
            opts = System.Enum.Parse(import_io, "None") if import_io else None
            b = blocks.Import(FileInfo(str(p)), opts)
            print(name, "imported:", b.Name)
        except Exception as e:
            print(name, "import FAIL:", str(e)[:400])

    # ---- OPC UA_1 子项 ----
    opc_items = []

    def find_opcua(item):
        try:
            if "OPC UA" in (item.Name or ""):
                opc_items.append(item)
        except Exception:
            pass
        try:
            for sub in item.DeviceItems:
                find_opcua(sub)
        except Exception:
            pass

    find_opcua(mini)
    print("opcua items:", [i.Name for i in opc_items])
    for it in opc_items:
        infos = [i.Name for i in it.GetAttributeInfos()]
        interesting = [n for n in infos if any(k in n.lower() for k in ("enabl", "activ", "server", "accessible"))]
        print("  attrs:", interesting)
        for cand in interesting:
            if "activ" in cand.lower() or "enabl" in cand.lower():
                try:
                    it.SetAttribute(cand, True)
                    print("  set", cand, "= True")
                except Exception as e:
                    print("  set fail:", cand, str(e)[:100])

    # ---- 编译（反射找 API）----
    compiled = False
    for obj, label in ((sw, "sw"), (blocks, "blocks")):
        ms = [m for m in obj.GetType().GetMethods() if m.Name == "Compile"]
        if ms:
            print(f"compile via {label}.Compile()")
            try:
                res = ms[0].Invoke(obj, None)
                compiled = True
                try:
                    errs = [m for m in res.Messages if str(m.State).upper() == "ERROR"]
                    print("compile errors:", len(errs))
                    for m in errs[:8]:
                        print("  ERR", m.Message)
                except Exception:
                    pass
            except Exception as e:
                print("compile invoke fail:", str(e)[:200])
            break
    if not compiled:
        print("no Compile API found; rely on download-time compile")

    project.Save()
    print("saved.")


if __name__ == "__main__":
    main()
