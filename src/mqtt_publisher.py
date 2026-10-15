"""Section 1.3 - MQTT publisher: first 2,000 generator readings -> energy/meters/{meter_id}.

Run SECOND (after the subscriber prints READY), in another terminal:
    docker compose exec app python src/mqtt_publisher.py

Counts dictionaries taken from the generator, messages handed to the client, and
messages acknowledged by the broker (QoS 1 PUBACK). Log: logs/publisher.log.
"""
import argparse
import itertools
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import smart_meter_simulator as sim  # noqa: E402  (unchanged generator in the project root)


def get_logger() -> logging.Logger:
    Path("logs").mkdir(exist_ok=True)
    log = logging.getLogger("publisher")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.StreamHandler(), logging.FileHandler("logs/publisher.log", mode="w")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    return log


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=2000, help="readings to take from the generator")
    args = parser.parse_args()
    log = get_logger()

    connected = threading.Event()
    lock = threading.Lock()
    acked = 0

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("Connection refused: %s", reason_code)
            return
        log.info("Connected to broker %s:%s", os.environ["MQTT_HOST"], os.environ["MQTT_PORT"])
        connected.set()

    def on_publish(client, userdata, mid, reason_code, properties):
        # For QoS 1 this fires when the broker's PUBACK arrives = "acknowledged by the broker".
        nonlocal acked
        with lock:
            acked += 1

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="energy-publisher")
    client.on_connect, client.on_publish = on_connect, on_publish
    client.connect(os.environ["MQTT_HOST"], int(os.environ["MQTT_PORT"]), keepalive=60)
    client.loop_start()
    if not connected.wait(timeout=10):
        raise SystemExit("Could not connect to the broker within 10 s")

    taken = handed_to_client = publish_errors = 0
    started = time.perf_counter()

    # islice takes exactly the first N dictionaries without materialising the whole stream.
    for reading in itertools.islice(sim.iter_readings(), args.count):
        taken += 1
        topic = f"energy/meters/{reading['meter_id']}"
        info = client.publish(topic, json.dumps(reading), qos=1, retain=False)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            handed_to_client += 1
        else:
            publish_errors += 1
            log.error("publish() failed rc=%s for reading %d (%s)", info.rc, taken, topic)
        if taken % 500 == 0:
            log.info("progress: taken=%d acked so far=%d", taken, acked)

    # Wait for the outstanding PUBACKs before disconnecting.
    deadline = time.monotonic() + 60
    while acked < handed_to_client and time.monotonic() < deadline:
        time.sleep(0.1)

    client.disconnect()
    client.loop_stop()
    elapsed = time.perf_counter() - started

    log.info("=" * 60)
    log.info("Dictionaries taken from generator : %d", taken)
    log.info("Messages handed to MQTT client    : %d", handed_to_client)
    log.info("publish() errors                  : %d", publish_errors)
    log.info("Messages acknowledged by broker   : %d", acked)
    log.info("Elapsed seconds                   : %.2f", elapsed)
    log.info("=" * 60)
    if acked != taken:
        log.warning("Difference of %d between taken and acknowledged - check the log", taken - acked)


if __name__ == "__main__":
    main()