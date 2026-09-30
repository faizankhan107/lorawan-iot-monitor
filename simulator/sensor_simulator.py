"""Publish fake ChirpStack-shaped application events.

This is a software simulator only.
No LoRa radio or physical gateway is involved.
"""

import json
import math
import os
import threading
import time

import paho.mqtt.client as mqtt

from monitor.codec import encode_sensor


DEVICES = (
    "a84041aabbccdd01",
    "a84041aabbccdd02",
    "a84041aabbccdd03",
)


def main():

    host = os.getenv(
        "MQTT_HOST",
        "localhost",
    )

    interval = float(
        os.getenv(
            "SIM_INTERVAL_SECONDS",
            "10",
        )
    )

    if interval <= 0:
        raise ValueError(
            "SIM_INTERVAL_SECONDS must be positive"
        )

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="monitor-demo-publisher",
    )

    connected = threading.Event()

    client.reconnect_delay_set(
        min_delay=1,
        max_delay=5,
    )

    def on_connect(
        client,
        userdata,
        flags,
        reason_code,
        properties,
    ):

        if reason_code == 0:
            connected.set()

            print(
                "Simulator connected to MQTT broker",
                flush=True,
            )

        else:
            connected.clear()

            print(
                f"MQTT connection rejected: {reason_code}",
                flush=True,
            )

    def on_disconnect(
        client,
        userdata,
        disconnect_flags,
        reason_code,
        properties,
    ):

        connected.clear()

        print(
            f"Simulator MQTT disconnected: {reason_code}",
            flush=True,
        )

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    while True:

        try:
            client.connect(
                host,
                1883,
                keepalive=60,
            )

            break

        except OSError as exc:

            print(
                f"MQTT broker unavailable ({exc}); "
                "retrying in 3 seconds",
                flush=True,
            )

            time.sleep(3)

    client.loop_start()

    try:

        n = 0

        while True:

            if not connected.wait(timeout=5):

                print(
                    "Waiting for MQTT reconnection...",
                    flush=True,
                )

                continue

            batch_ok = True

            for i, dev_eui in enumerate(DEVICES):

                temperature = (
                    22
                    + i
                    + 2 * math.sin(n / 8 + i)
                )

                humidity = (
                    49
                    + 8 * math.sin(n / 12 + i)
                )

                event = {
                    "deviceInfo": {
                        "devEui": dev_eui,
                        "deviceName": (
                            f"Demo sensor {i + 1}"
                        ),
                    },
                    "data": encode_sensor(
                        temperature,
                        humidity,
                    ),
                    "rxInfo": [
                        {
                            "rssi": -65 - 9 * i,
                            "snr": round(
                                8 - 2 * i,
                                1,
                            ),
                        }
                    ],
                }

                topic = (
                    "demo/local/device/"
                    f"{dev_eui}/event/up"
                )

                try:

                    result = client.publish(
                        topic,
                        json.dumps(event),
                        qos=1,
                    )

                    result.wait_for_publish(
                        timeout=5
                    )

                except RuntimeError as exc:

                    batch_ok = False

                    print(
                        "Publish interrupted "
                        f"({exc}); waiting for reconnect",
                        flush=True,
                    )

                    break

            if batch_ok:
                n += 1

            time.sleep(interval)

    finally:

        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
