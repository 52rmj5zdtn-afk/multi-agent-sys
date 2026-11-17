"""plcsim_ctl.py — PLCSIM Advanced 虚拟 PLC 控制器（pythonnet）

用法:
    python scripts/plcsim_ctl.py status              # 查看实例状态
    python scripts/plcsim_ctl.py create              # 创建 1516TF 实例并上电运行
    python scripts/plcsim_ctl.py run                 # 切换到 RUN
    python scripts/plcsim_ctl.py delete              # 删除实例
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

API_DLL = Path(r"C:\Program Files (x86)\Common Files\Siemens\PLCSIMADV\API\7.0"
               ) / "Siemens.Simatic.Simulation.Runtime.Api.x64.dll"
INSTANCE_NAME = "Line03PLC"

import clr  # noqa: E402

ASM = clr.AddReference(str(API_DLL))
from Siemens.Simatic.Simulation.Runtime import (  # noqa: E402
    ECPUType, EOperatingState, SimulationRuntimeManager)


def _instances():
    """已注册实例列表（名字/句柄），API 差异用反射兜底。"""
    for m in SimulationRuntimeManager.GetMethods():
        if m.Name in ("GetInstances", "GetInstanceInfos", "GetRegisteredInstances"):
            try:
                res = getattr(SimulationRuntimeManager, m.Name)()
                return list(res)
            except Exception:
                return []
    return []


def _get(name: str):
    try:
        return SimulationRuntimeManager.CreateInterface(name)
    except Exception:
        return None


def create() -> None:
    inst = _get(INSTANCE_NAME)
    if inst is None:
        print(f"创建实例 {INSTANCE_NAME} (CPU1516TF)...")
        inst = SimulationRuntimeManager.RegisterInstance(ECPUType.CPU1516TF, INSTANCE_NAME)
    else:
        print(f"实例 {INSTANCE_NAME} 已存在，复用")
    rc = inst.PowerOn()
    print(f"PowerOn → {rc}")
    for _ in range(60):
        time.sleep(1)
        state = inst.OperatingState
        print(f"  状态: {state}")
        if state not in (EOperatingState.Invalid, EOperatingState.Off,
                         EOperatingState.Booting, EOperatingState.NotReachable):
            break
    print(f"控制器 IP: {list(inst.ControllerIP)}")
    inst.Run()
    time.sleep(2)
    print(f"最终状态: {inst.OperatingState} (期望 Run/RunLight)")


def status() -> None:
    inst = _get(INSTANCE_NAME)
    if inst is None:
        print(f"实例 {INSTANCE_NAME} 不存在")
        return
    print(f"实例: {inst.Name} 控制器: {inst.ControllerName}")
    print(f"状态: {inst.OperatingState}")
    print(f"IP: {list(inst.ControllerIP)}")


def delete() -> None:
    inst = _get(INSTANCE_NAME)
    if inst is None:
        print("实例不存在")
        return
    try:
        inst.PowerOff()
        time.sleep(2)
    except Exception as e:  # noqa: BLE001
        print(f"PowerOff: {e}")
    inst.UnregisterInstance()
    print("已删除")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"create": create, "status": status, "run": create, "delete": delete}[cmd]()
