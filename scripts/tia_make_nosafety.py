"""在项目副本上删除安全程序（S7-1500F 不带安全程序即可免 F 密码下载）。

流程：附加 → 保存原项目 → SaveAs 副本 → 删除 "02_Safety(安全)" 组全部块与组
→ 编译软件 → 保存副本。原项目文件不被修改。
用法: python scripts/tia_make_nosafety.py [--dry-run]
"""
from __future__ import annotations

import sys
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
import os
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402
ASM = clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))

from Siemens.Engineering import TiaPortal  # noqa: E402

DRY = "--dry-run" in sys.argv
COPY_PATH = r"F:\Sicar程序理解\巢湖三拉环线\Line03MCPBoZhonSiemens20260401_NoSafety.ap20"

SC_TYPE = None
try:
    types = ASM.GetTypes()
except Exception as e:
    types = e.Types
for t in types:
    if t is not None and t.Name == "SoftwareContainer":
        SC_TYPE = t


def attach():
    for proc in TiaPortal.GetProcesses():
        try:
            tia = proc.Attach()
            if tia.Projects.Count > 0:
                return tia, tia.Projects[0]
        except Exception:
            continue
    raise RuntimeError("no TIA process with open project")


def find_cpu_item(project):
    found = []

    def walk(item):
        try:
            nm = item.Name or ""
            if "1516" in nm and "PLC" in nm.upper():
                found.append(item)
                return
        except Exception:
            pass
        try:
            for sub in item.DeviceItems:
                walk(sub)
        except Exception:
            pass

    for dev in project.Devices:
        if not found:
            walk(dev)
    return found[0] if found else None


def get_software(cpu_item):
    sc = cpu_item.GetService[SC_TYPE]()
    return sc.Software if sc else None


def find_safety_group(block_group):
    """按组名前缀 02_Safety 匹配（SICAR 约定）。"""
    for g in block_group.Groups:
        name = g.Name or ""
        if "SAFETY" in name.upper() or "安全" in name:
            return g
    return None


def main() -> None:
    tia, project = attach()
    print("original:", project.Path)
    if "NoSafety" not in str(project.Path):
        if not DRY:
            project.Save()
            print("original saved")
            if Path(COPY_PATH).exists():
                raise SystemExit(f"copy exists: {COPY_PATH}")
            from System.IO import DirectoryInfo
            project.SaveAs(DirectoryInfo(COPY_PATH))
            print("SaveAs ->", COPY_PATH)

    project = tia.Projects[0]
    print("working:", project.Path)
    assert "NoSafety" in str(project.Path), "SaveAs 未生效!"

    cpu = find_cpu_item(project)
    sw = get_software(cpu)
    print("software:", sw.GetType().FullName)

    # 根块清单（检查是否有 F-Call 残留）
    try:
        root_blocks = [b.Name for b in sw.BlockGroup.Blocks]
        print("root blocks:", root_blocks)
    except Exception as e:
        print("root blocks err:", e)

    sg = find_safety_group(sw.BlockGroup)
    if sg is None:
        raise SystemExit("safety group not found")
    print("safety group:", sg.Name)

    # 收集全部子组与块
    groups, blocks = [], []

    def collect(g):
        for b in g.Blocks:
            blocks.append(b)
        for sub in g.Groups:
            groups.append(sub)
            collect(sub)

    collect(sg)
    print(f"to delete: {len(blocks)} blocks, {len(groups)} subgroups")

    if not DRY:
        for b in blocks:
            try:
                b.Delete()
            except Exception as e:
                print("  block delete FAIL:", b.Name, "|", e)
        # 子组从深到浅删除（收集顺序即深度优先顺序，倒序删除）
        for g in reversed(groups):
            try:
                g.Delete()
            except Exception as e:
                print("  group delete FAIL:", g.Name, "|", e)
        try:
            sg.Delete()
        except Exception as e:
            print("  safety root delete FAIL:", e)
        print("deletion done")

    # ---- 编译 ----
    compile_methods = [m.Name for m in sw.GetType().GetMethods() if "Compile" in m.Name]
    print("compile methods on PlcSoftware:", compile_methods)
    if DRY:
        return
    result = None
    if "Compile" in compile_methods:
        result = sw.Compile()
    if result is not None:
        print("compile result type:", result.GetType().FullName)
        try:
            msgs = result.Messages
            states = {}
            for m in msgs:
                states[m.State] = states.get(m.State, 0) + 1
                if str(m.State).upper() in ("ERROR", "WARNING"):
                    print("  ", m.State, "|", m.Message)
            print("compile message states:", states)
        except Exception as e:
            print("messages err:", e)
    project.Save()
    print("copy saved.")


if __name__ == "__main__":
    main()
