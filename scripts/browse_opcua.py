"""浏览 S7-1500 OPC UA 地址空间，导出可用节点 → deployment/opcua_nodes_browse.json。

用法: python scripts/browse_opcua.py [endpoint] [--max-depth 4] [--max-children 200]
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "deployment" / "opcua_nodes_browse.json"


async def main() -> None:
    endpoint = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "opc.tcp://192.168.1.6:4840"
    max_depth = 4
    max_children = 200

    from asyncua import Client

    client = Client(endpoint)
    client.session_timeout = 10000
    async with client:
        root = client.nodes.objects
        out = []

        async def walk(node, path, depth):
            if depth > max_depth:
                return
            try:
                children = await node.get_children()
            except Exception:
                return
            count = 0
            for ch in children:
                if count >= max_children:
                    break
                count += 1
                try:
                    browsename = (await ch.read_browse_name()).Name
                    nclass = (await ch.read_node_class()).name
                except Exception:
                    continue
                cur = f"{path}/{browsename}"
                entry = {"path": cur, "class": nclass}
                if nclass == "Variable":
                    try:
                        dv = await ch.read_data_value()
                        entry["value"] = str(dv.Value.Value)[:80]
                        entry["type"] = str(dv.Value.VariantType)
                    except Exception:
                        pass
                out.append(entry)
                if nclass == "Object" or nclass == "ObjectType":
                    await walk(ch, cur, depth + 1)

        await walk(root, "Objects", 0)
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
        print(f"endpoint={endpoint} nodes={len(out)}")
        # 按顶层分组统计
        from collections import Counter
        c = Counter(e["path"].split("/")[1] if "/" in e["path"] else e["path"] for e in out)
        for k, v in sorted(c.items()):
            print(f"  {k}: {v}")
        for e in out[:40]:
            print("   ", e["path"], e.get("class"), e.get("value", ""))


if __name__ == "__main__":
    asyncio.run(main())
