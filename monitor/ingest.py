"""Accept ChirpStack v4 MQTT uplinks and explicitly labeled synthetic uplinks."""
import json
import re

from .codec import decode_sensor

TOPIC = re.compile(
    r"^(?P<source>application|demo)/[^/]+/device/(?P<dev_eui>[0-9a-fA-F]{16})/event/up$"
)


def parse_uplink(topic: str, raw: bytes) -> dict:
    match = TOPIC.fullmatch(topic)
    if not match:
        raise ValueError("unsupported topic")
    try:
        event = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON") from exc
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    info = event.get("deviceInfo") or {}
    if not isinstance(info, dict):
        raise ValueError("invalid deviceInfo")
    if info.get("devEui") and info["devEui"].lower() != match["dev_eui"].lower():
        raise ValueError("devEui does not match topic")
    if isinstance(event.get("data"), str):
        measurements = decode_sensor(event["data"])
    else:
        obj = event.get("object")
        if not isinstance(obj, dict):
            raise ValueError("missing supported sensor data")
        try:
            measurements = {
                "temperature_c": float(obj["temperature_c"]),
                "humidity_pct": float(obj["humidity_pct"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("missing supported decoded fields") from exc
        if not (-327.68 <= measurements["temperature_c"] <= 327.67
                and 0 <= measurements["humidity_pct"] <= 100):
            raise ValueError("decoded measurement out of range")
    rx = event.get("rxInfo") or []
    first = rx[0] if isinstance(rx, list) and rx and isinstance(rx[0], dict) else {}
    try:
        rssi = int(first["rssi"]) if first.get("rssi") is not None else None
        snr = float(first["snr"]) if first.get("snr") is not None else None
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid radio metrics") from exc
    return {
        "source": "simulated" if match["source"] == "demo" else "chirpstack",
        "dev_eui": match["dev_eui"].lower(),
        "name": str(info.get("deviceName") or match["dev_eui"])[:80],
        "rssi": rssi,
        "snr": snr,
        **measurements,
    }
