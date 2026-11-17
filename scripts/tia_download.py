"""tia_download.py — Openness 下载到 PLCSIM（基础版）

前提: PLCSIM(S7PLCSIMV20) 已运行, TIA 已打开项目, python.exe 在白名单。

流程: attach → 找 CPU → 取 PlcSoftware → DownloadProvider →
Download(configuration, delegate, delegate, DownloadOptions.Software)
delegate 对每个下载配置选择默认允许并打印类型（诊断）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

API_DIR = Path(r"D:\TIA V20\Portal V20\PublicAPI\V20")
BIN_DIR = Path(r"D:\TIA V20\Portal V20\Bin")
os.environ["PATH"] = f"{API_DIR};{BIN_DIR};" + os.environ.get("PATH", "")

import clr  # noqa: E402

clr.AddReference(str(API_DIR / "Siemens.Engineering.dll"))
import System  # noqa: E402
from Siemens.Engineering import TiaPortal  # noqa: E402

ASM = System.Reflection.Assembly.LoadFrom(str(API_DIR / "Siemens.Engineering.dll"))


def t(name: str):
    return ASM.GetType(name)


def attach():
    for proc in TiaPortal.GetProcesses():
        if proc.ProjectPath:
            tia = proc.Attach()
            return tia, tia.Projects[0]
    raise RuntimeError("TIA 无打开项目")


def find_cpu(project):
    """找主 PLC (Zone1_PLC1516TF) 的设备项。"""
    sc_type = t("Siemens.Engineering.HW.Features.SoftwareContainer")
    for dev in project.Devices:
        found_item = None
        has_plc = False
        stack = list(dev.DeviceItems)
        while stack:
            it = stack.pop()
            try:
                if "Zone1_PLC1516TF" in (it.Name or ""):
                    has_plc = True
            except Exception:
                pass
            try:
                m = it.GetType().GetMethod("GetService")
                svc = m.MakeGenericMethod(sc_type).Invoke(it, None)
                if svc is not None and svc.Software is not None and found_item is None:
                    found_item = it
            except Exception:
                pass
            try:
                stack.extend(list(it.DeviceItems))
            except Exception:
                pass
        if has_plc and found_item is not None:
            print(f"cpu item: {found_item.Name} (device {dev.Name})")
            return found_item
    raise RuntimeError("未找到主 PLC")


def get_service(obj, type_name: str):
    """泛型 GetService<T>()  via MakeGenericMethod（pythonnet 可靠路径）。"""
    ctype = t(type_name)
    if ctype is None:
        return None
    m = obj.GetType().GetMethod("GetService")
    if m is None:
        return None
    return m.MakeGenericMethod(ctype).Invoke(obj, None)


def get_plc_software(cpu_item):
    candidates = [
        "Siemens.Engineering.HW.Features.SoftwareContainer",
        "Siemens.Engineering.SW.SoftwareContainer",
        "Siemens.Engineering.SoftwareContainer",
    ]
    for cname in candidates:
        try:
            svc = get_service(cpu_item, cname)
            if svc is not None:
                print(f"GetService({cname}) → {svc.GetType().FullName}")
                sw = svc.Software
                print(f"  Software = {sw.GetType().FullName}")
                return sw
        except Exception as e:  # noqa: BLE001
            print(f"GetService({cname}) → {type(e).__name__}: {str(e)[:200]}")
    raise RuntimeError("无法获取 PlcSoftware")

def main() -> None:
    tia, project = attach()
    print(f"project: {project.Name}")
    cpu = find_cpu(project)
    print(f"cpu: {cpu.Name}")

    sw = get_plc_software(cpu)
    dp = get_service(cpu, "Siemens.Engineering.Download.DownloadProvider")
    if dp is None:
        raise RuntimeError("无 DownloadProvider")
    print("DownloadProvider OK")

    # 项目级仿真支持（PLCSIM Advanced 虚拟 PLC 下载前提）
    try:
        print("仿真编译支持: ", project.IsSimulationDuringBlockCompilationEnabled,
              "| 虚拟PLC支持: ", project.IsVirtualPlcDuringBlockCompilationEnabled)
        project.IsVirtualPlcDuringBlockCompilationEnabled = True
        project.IsSimulationDuringBlockCompilationEnabled = True
        print("已启用项目仿真支持")
    except Exception as e:
        print(f"设置仿真属性: {e}")

    conn_conf = dp.Configuration
    target = None
    for mode in conn_conf.Modes:
        for pcif in mode.PcInterfaces:
            if "PLCSIM" in pcif.Name:
                for tgt in pcif.TargetInterfaces:
                    if target is None:
                        target = tgt
                        print(f"目标: mode={mode.Name} pcif={pcif.Name} tgt={tgt.Name}")
    if target is None:
        raise RuntimeError("未找到 PLCSIM 虚拟网卡目标")
    ok = conn_conf.ApplyConfiguration(target)
    print(f"ApplyConfiguration → {ok}")

    from Siemens.Engineering.Download import (  # noqa: E402
        DownloadConfigurationDelegate, DownloadOptions)

    def _set_first_current_selection(cfg, keyword=None):
        p = cfg.GetType().GetProperty("CurrentSelection")
        if p is None:
            return False
        vals = [f.Name for f in p.PropertyType.GetFields()]
        pick = None
        for prefer in (keyword, "Accept", "Yes", "OK", "Continue", "Consistent", "StopAll", "LoadAnyway"):
            if prefer and prefer in vals:
                pick = prefer
                break
        if pick is None and vals:
            pick = vals[0]
        if pick:
            v = System.Enum.Parse(p.PropertyType, pick)
            p.SetValue(cfg, v, None)
            print(f"    → {cfg.GetType().Name}.CurrentSelection = {pick}")
            return True
        return False

    def configure(cfg):
        tn = cfg.GetType().Name
        print(f"  [config] {tn}")
        if tn == "CheckBeforeDownload":
            p = cfg.GetType().GetProperty("Checked")
            p.SetValue(cfg, True, None)
            print("    → Checked = True")
            return
        if tn == "BlockBindingPassword" or tn == "ModuleWriteAccessPassword":
            print("    !! 需要密码，尝试空密码")
            try:
                cfg.SetPassword("")   # 可能抛异常，届时下载失败并提示
            except Exception as e:
                print(f"    SetPassword 失败: {e}")
            return
        if tn == "TargetForSoftwareSelections":
            _set_first_current_selection(cfg, "PlcSimulationAdvanced")
            return
        _set_first_current_selection(cfg)

    del_pre = DownloadConfigurationDelegate(configure)
    del_post = DownloadConfigurationDelegate(configure)

    online_ip = os.environ.get("PLCSIM_IP", "192.168.1.6")
    opt = os.environ.get("DL_OPT", "HW_SW")
    addrs = target.Addresses
    online_address = addrs.Create(online_ip)
    print(f"在线地址: {online_ip} 选项: {opt}")

    if opt == "SW":
        options = DownloadOptions.Software
    elif opt == "HW":
        options = DownloadOptions.Hardware
    else:
        options = DownloadOptions.Hardware | DownloadOptions.Software

    print("开始下载到 PLCSIM Advanced ...")
    try:
        result = dp.Download(target, online_address, del_pre, del_post, options)
    except Exception as e:
        print("DOWNLOAD-EXC:", type(e).__name__)
        depth = 0
        cur = e
        while cur is not None and depth < 8:
            msg = str(cur)
            print(f"--- inner[{depth}] {type(cur).__name__}: {msg[:1500]}")
            cur = getattr(cur, "InnerException", None)
            depth += 1
        raise
    print("结果状态:", result.State)
    try:
        for msg in result.Messages:
            print("  ", msg.State, msg.Description)
    except Exception:
        pass


if __name__ == "__main__":
    main()
