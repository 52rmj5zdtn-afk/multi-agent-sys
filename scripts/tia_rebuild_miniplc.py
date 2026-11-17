"""重建 MiniPLC（保留自动安全结构！），一次完成全部配置 + 双编译验证。

此前错误根源：删除 F 块后 F 运行时组悬空。本版保留空安全程序（无密码，下载不需登录）。
"""
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
from Siemens.Engineering.HW import OpcUaPurchasedLicense  # noqa: E402,F401

CPU_TYPE = "OrderNumber:6ES7 516-3UP10-0AB0/V4.0"

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


def get_project():
    for proc in TiaPortal.GetProcesses():
        try:
            t = proc.Attach()
            if t.Projects.Count > 0:
                return t, t.Projects[0]
        except Exception:
            continue
    raise RuntimeError("no project")


def compile_and_report(cpu, sw, label):
    ICOMP = find_type("Siemens.Engineering.Compiler.ICompilable")
    out = []
    for svc_holder, name in ((cpu, "hw"), (sw, "sw")):
        res = svc_holder.GetService[ICOMP]().Compile()
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
        out.append((name, len(errs), msgs))
    for name, n, msgs in out:
        print(f"[{label}] {name} compile errors: {n}", flush=True)
        for s, d in msgs:
            if s in ("Error", "Warning"):
                print(f"    [{s}] {d}", flush=True)
    return all(n == 0 for _, n, _ in out)


def main() -> None:
    tia, project = get_project()
    # 1) 删除旧 MiniPLC
    for dev in list(project.Devices):
        if any("MiniPLC" in (i.Name or "") for i in dev.DeviceItems):
            nm = dev.Name
            try:
                dev.Delete()
                print("deleted device:", nm, flush=True)
            except Exception as e:
                print("delete fail:", str(e)[:150], flush=True)
            break
    time.sleep(1)

    # 2) 重建（自动带安全结构）
    mini = project.Devices.CreateWithItem(CPU_TYPE, "MiniPLC", "2")
    print("recreated:", mini.Name, flush=True)

    cpu = next(i for i in mini.DeviceItems if (i.Name or "") == "MiniPLC")
    SC = find_type("Siemens.Engineering.HW.Features.SoftwareContainer")
    sw = cpu.GetService[SC]().Software
    blocks = sw.BlockGroup.Blocks

    # 3) 网络：X1=192.168.1.6 入网；X2/GBIT 避开同子网
    NI = find_type("Siemens.Engineering.HW.Features.NetworkInterface")

    def walk_net(item):
        try:
            lbl = item.GetAttribute("Label") or ""
        except Exception:
            lbl = ""
        try:
            if lbl in ("X1", "X2", "X3"):
                svc = item.GetService[NI]()
                if svc:
                    for n in svc.Nodes:
                        if lbl == "X1":
                            n.SetAttribute("Address", "192.168.1.6")
                            n.SetAttribute("SubnetMask", "255.255.255.0")
                            try:
                                n.CreateAndConnectToSubnet("MiniPLC_Net")
                            except Exception as e:
                                print("subnet connect:", str(e)[:100], flush=True)
                        elif lbl == "X2":
                            n.SetAttribute("Address", "192.168.2.1")
                        else:
                            n.SetAttribute("Address", "192.168.3.1")
                        print(f"{lbl} ->", n.GetAttribute("Address"), flush=True)
        except Exception:
            pass
        try:
            for s in item.DeviceItems:
                walk_net(s)
        except Exception:
            pass

    walk_net(mini)

    # 4) DB 导入（标准布局）
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
            print(name, "imported", flush=True)
        except Exception as e:
            print(name, "FAIL:", str(e)[:200], flush=True)

    # 5) CPU 属性
    cpu.SetAttribute("OpcUaPurchasedLicense", OpcUaPurchasedLicense.SimaticOpcUaS71500Large)
    cpu.SetAttribute("ProtectionEnablePutGetCommunication", True)
    cur = cpu.GetAttribute("PlcAccessControlConfiguration")
    for f in cur.GetType().GetFields():
        if f.Name == "Disabled":
            cpu.SetAttribute("PlcAccessControlConfiguration",
                             System.Enum.Parse(cur.GetType(), "Disabled"))
            print("AccessControl -> Disabled", flush=True)
            break

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
        it.SetAttribute("OpcUaServer", True)
        print("OpcUaServer = True", flush=True)

    # 6) 双编译
    ok = compile_and_report(cpu, sw, "rebuild")
    project.Save()
    print("saved. all_ok =", ok, flush=True)


if __name__ == "__main__":
    main()
