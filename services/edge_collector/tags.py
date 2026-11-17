"""tags.py — OPC UA 标签映射表加载与快照→遥测行转换
===================================================
标签表（YAML）是边缘采集与 PLC 程序的接口契约：
  device_id → {cycle_tag, features: [{name, node, deadband}]}
采集循环把 OPC UA 快照（node → value）按表映射为
  device_id → {feature_name → value}，即 device_telemetry.readings 的同名字段。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_TAGS = Path(__file__).parent / "tags_weld.yaml"


@dataclass(frozen=True)
class FeatureTag:
    name: str
    node: str
    deadband: float = 0.0


@dataclass(frozen=True)
class DeviceTags:
    device_id: str
    cycle_tag: str | None
    features: tuple[FeatureTag, ...]

    @property
    def feature_names(self) -> list[str]:
        return [f.name for f in self.features]


@dataclass(frozen=True)
class TagTable:
    line: str
    station: str
    opcua_endpoint: str
    opcua_namespace: int
    devices: dict[str, DeviceTags]

    def map_snapshot(self, device_id: str, values: dict[str, float]) -> dict[str, float]:
        """OPC UA 快照（node 路径 → 值）→ 特征名 → 值（只保留表内声明的特征）。"""
        dev = self.devices[device_id]
        node_to_name = {f.node: f.name for f in dev.features}
        return {node_to_name[node]: val for node, val in values.items() if node in node_to_name}


def load_tag_table(path: str | Path = DEFAULT_TAGS) -> TagTable:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    devices = {}
    for device_id, dev in raw["devices"].items():
        feats = tuple(FeatureTag(name=f["name"], node=f["node"], deadband=float(f.get("deadband", 0.0)))
                      for f in dev["features"])
        devices[device_id] = DeviceTags(device_id=device_id, cycle_tag=dev.get("cycle_tag"),
                                        features=feats)
    return TagTable(
        line=raw["line"], station=raw["station"],
        opcua_endpoint=raw["opcua"]["endpoint"],
        opcua_namespace=int(raw["opcua"].get("namespace", 3)),
        devices=devices,
    )
