"""Section 1.3 - MQTT subscriber: energy/meters/# (QoS 1) -> energy_live.

Run FIRST, in its own terminal:
    docker compose exec app python src/mqtt_subscriber.py

It waits for messages, parses each JSON payload, inserts the eight fields into
energy_live in committed batches, and stops automatically once no message has
arrived for --idle seconds (or on Ctrl+C). A log is written to logs/subscriber.log.
"""
import argparse
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import psycopg

TOPIC = "energy/meters/#"
FIELDS = ("meter_id", "event_time", "received_time", "power_kw",
          "voltage_v", "current_a", "frequency_hz", "energy_kwh")
INSERT_SQL = (f"INSERT INTO energy_live ({', '.join(FIELDS)}) "
              f"VALUES ({', '.join(['%s'] * len(FIELDS))})")


def get_logger() -> logging.Logger:
    Path("logs").mkdir(exist_ok=True)
    log = logging.getLogger("subscriber")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.StreamHandler(), logging.FileHandler("logs/subscriber.log", mode="w")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    return log


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--idle", type=float, default=15, help="stop after this many quiet seconds")
    parser.add_argument("--batch", type=int, default=200, help="rows per committed insert batch")
    args = parser.parse_args()
    log = get_logger()

    # The MQTT network thread puts messages here; the main thread does the database work.
    # Keeping slow database calls out of the network thread stops acknowledgements stalling.
    inbox: queue.Queue = queue.Queue()
    subscribed = threading.Event()
    stats = {"received": 0, "dup_flag": 0}

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("Connection refused: %s", reason_code)
            return
        log.info("Connected to broker %s:%s", os.environ["MQTT_HOST"], os.environ["MQTT_PORT"])
        client.subscribe(TOPIC, qos=1)   # subscribing here means it is redone after any reconnect

    def on_subscribe(client, userdata, mid, reason_codes, properties):
        log.info("Subscribed to %s, granted QoS %s", TOPIC, [rc.value for rc in reason_codes])
        subscribed.set()

    def on_message(client, userdata, msg):
        stats["received"] += 1
        if msg.dup:   # broker is re-sending a message whose acknowledgement it did not get
            stats["dup_flag"] += 1
            log.warning("REDELIVERY (dup flag) topic=%s mid=%s", msg.topic, msg.mid)
        inbox.put((msg.topic, msg.payload))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="energy-subscriber")
    client.on_connect, client.on_subscribe, client.on_message = on_connect, on_subscribe, on_message
    client.connect(os.environ["MQTT_HOST"], int(os.environ["MQTT_PORT"]), keepalive=60)
    client.loop_start()   # background network thread

    if not subscribed.wait(timeout=10):
        raise SystemExit("Subscription not confirmed within 10 s - is EMQX running?")

    parsed = parse_errors = inserted = insert_failed = 0
    batch: list[tuple] = []
    last_message = None
    last_logged = 0

    with psycopg.connect() as conn:
        start_rows = conn.execute("SELECT count(*) FROM energy_live").fetchone()[0]
        log.info("energy_live rows at start: %d", start_rows)
        log.info("READY - start the publisher now (stops after %.0f s without messages)", args.idle)

        def flush() -> None:
            nonlocal inserted, insert_failed
            if not batch:
                return
            try:
                with conn.cursor() as cur:
                    cur.executemany(INSERT_SQL, batch)
                conn.commit()
                inserted += len(batch)
            except Exception:
                conn.rollback()
                insert_failed += len(batch)
                log.exception("Insert batch of %d rows FAILED", len(batch))
            batch.clear()

        try:
            while True:
                try:
                    topic, payload = inbox.get(timeout=0.5)
                    last_message = time.monotonic()
                    try:
                        record = json.loads(payload)
                        row = tuple(record[field] for field in FIELDS)
                        if topic != f"energy/meters/{record['meter_id']}":
                            log.warning("Topic/meter mismatch: %s vs %s", topic, record["meter_id"])
                        batch.append(row)
                        parsed += 1
                    except (ValueError, KeyError, TypeError) as exc:
                        parse_errors += 1
                        log.error("PARSE ERROR on %s: %s | payload=%r", topic, exc, payload[:200])
                except queue.Empty:
                    pass

                if len(batch) >= args.batch or (batch and inbox.empty()):
                    flush()
                if stats["received"] // 500 > last_logged // 500:
                    last_logged = stats["received"]
                    log.info("progress: received=%d inserted=%d", stats["received"], inserted)

                idle_for = time.monotonic() - last_message if last_message else 0
                if last_message and idle_for > args.idle and inbox.empty():
                    log.info("No messages for %.0f s - finishing", args.idle)
                    break
        except KeyboardInterrupt:
            log.info("Ctrl+C - finishing")

        flush()
        client.disconnect()
        client.loop_stop()
        final_rows = conn.execute("SELECT count(*) FROM energy_live").fetchone()[0]

    log.info("=" * 60)
    log.info("Messages received by subscriber : %d", stats["received"])
    log.info("  of which flagged as redelivery: %d", stats["dup_flag"])
    log.info("Parsed OK                       : %d", parsed)
    log.info("Parse errors                    : %d", parse_errors)
    log.info("Rows inserted and committed     : %d", inserted)
    log.info("Rows in failed insert batches   : %d", insert_failed)
    log.info("energy_live row count (query)   : %d", final_rows)
    log.info("=" * 60)


if __name__ == "__main__":
    main()