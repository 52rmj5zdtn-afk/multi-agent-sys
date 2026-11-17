"""services/edge_collector — S7-1500 → OPC UA → MQTT 边缘采集（M1）

data flow: opcua_source/simulator → collector(死区字段预留, 断线补传) → MQTT
"""
