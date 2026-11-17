"""遍历候选 DB 子树，记录每个变量叶子的可读性/值/NodeId → deployment/opcua_weld_subtrees.json"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "deployment" / "opcua_weld_subtrees.json"

DBS = [
    "OPMODE07_DB", "OPMODE07_EM040_DB", "GLOBAL_LASERWELDING_HMI_IF_DB",
    "PHM_DB", "CycleTimeRecord_DB", "PRODUCTION_DATA_DB", "HEART_DB",
    "Laser_DatCol_IDB", "OPMODE07_EM040_Laser_FB_IDB",
    "OPMODE07_EM040_Vision2D_DB", "OPMODE07_EM040_Vision3D_DB",
]


async def main() -> None:
    endpoint = sys.argv[1] if len(sys.argv) > 1 else "opc.tcp://192.168.1.6:4840"
    from asyncua import Client

    client = Client(endpoint)
    async with client:
        root = client.nodes.objects
        for ch in await root.get_children():
            if (await ch.read_browse_name()).Name == "DeviceSet":
                ds = ch
                break
        for c in await ds.get_children():
            if "Zone1_PLC1516TF" in (await c.read_browse_name()).Name:
                plc = c
                break
        out = []

        async def walk(node, path, depth):
            if depth > 14:
                return
            try:
                kids = await node.get_children()
            except Exception:
                return
            for k in kids:
                try:
                    bn = (await k.read_browse_name()).Name
                except Exception:
                    continue
                try:
                    nc = (await k.read_node_class()).name
                except Exception:
                    nc = "?"
                cur = f"{path}/{bn}"
                if nc == "Variable":
                    entry = {"path": cur, "readable": False}
                    try:
                        dv = await k.read_data_value()
                        entry["readable"] = True
                        entry["value"] = str(dv.Value.Value)[:70]
                        entry["type"] = str(dv.Value.VariantType)
                    except Exception as e:
                        entry["err"] = str(e)[:50]
                    try:
                        entry["id"] = (await k.read_node_id()).to_string()
                    except Exception:
                        pass
                    out.append(entry)
                    # structs may nest under variables (S7 UDT)
                    await walk(k, cur, depth + 1)
                elif nc == "Object":
                    await walk(k, cur, depth + 1)

        for ch in await plc.get_children():
            bn = (await ch.read_browse_name()).Name
            if bn not in ("DataBlocksGlobal", "DataBlocksInstance"):
                continue
            for db in await ch.get_children():
                dbn = (await db.read_browse_name()).Name
                if dbn in DBS:
                    print("walking", bn + "/" + dbn, flush=True)
                    await walk(db, f"{bn}/{dbn}", 0)

        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
        rd = [e for e in out if e.get("readable")]
        print(f"total={len(out)} readable={len(rd)}")


if __name__ == "__main__":
    asyncio.run(main())
