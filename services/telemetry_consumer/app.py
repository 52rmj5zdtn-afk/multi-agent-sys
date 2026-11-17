"""app.py — 遥测消费者入口
==========================
生产（HTTP sink，复用服务端校验/鉴权）:
    python -m services.telemetry_consumer.app \
        --line line03 --sink http --api http://192.168.0.30:8000 \
        --user telemetry_bot --password ***

本地联调（直写库）:
    python -m services.telemetry_consumer.app --sink db
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal

import paho.mqtt.client as mqtt

from services.telemetry_consumer.consumer import DbSink, HttpSink, TelemetryConsumer


def main() -> None:
    parser = argparse.ArgumentParser(description="MQTT → 遥测管线消费者")
    parser.add_argument("--line", default="line03")
    parser.add_argument("--sink", choices=["http", "db"], default="db")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="FastAPI 地址（http sink）")
    parser.add_argument("--user", default=None, help="服务账号（http sink，需班组长及以上）")
    parser.add_argument("--password", default=None)
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--flush-interval", type=float, default=2.0)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("telemetry_consumer")

    if args.sink == "http":
        if not args.user or not args.password:
            parser.error("--sink http 需要 --user/--password（班组长及以上角色的服务账号）")
        sink = HttpSink(args.api, args.user, args.password)
    else:
        sink = DbSink()

    consumer = TelemetryConsumer(sink, line=args.line, flush_interval=args.flush_interval)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="telemetry-consumer")
    client.on_connect = lambda c, u, f, rc, p: (rc == 0) and c.subscribe(consumer.topic_filter(), qos=1)
    client.on_message = lambda c, u, msg: consumer.on_message(msg.topic, msg.payload)
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
    client.loop_start()
    log.info(f"已订阅 {consumer.topic_filter()}，sink={args.sink}")

    stop = asyncio.Event()
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async def run() -> None:
        while not stop.is_set():
            await asyncio.sleep(consumer.flush_interval)
            await consumer.flush()
        await consumer.flush()
        log.info(f"退出：收到 {consumer.received} 条，写入 {consumer.written} 条，"
                 f"冻结去重 {consumer.skipped_frozen} 条")
    try:
        loop.run_until_complete(run())
    finally:
        client.loop_stop()
        client.disconnect()
        if isinstance(sink, HttpSink):
            loop.run_until_complete(sink.close())
        loop.close()


if __name__ == "__main__":
    main()
