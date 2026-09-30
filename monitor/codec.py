"""Tiny example payload: signed centi-degrees C and unsigned centi-percent RH."""
import base64
import binascii
import struct


def encode_sensor(temperature_c: float, humidity_pct: float) -> str:
    if not (-327.68 <= temperature_c <= 327.67 and 0 <= humidity_pct <= 100):
        raise ValueError("sensor value out of range")
    payload = struct.pack(">hH", round(temperature_c * 100), round(humidity_pct * 100))
    return base64.b64encode(payload).decode("ascii")


def decode_sensor(value: str) -> dict:
    try:
        payload = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 data") from exc
    if len(payload) != 4:
        raise ValueError("expected four payload bytes")
    temperature, humidity = struct.unpack(">hH", payload)
    if humidity > 10000:
        raise ValueError("humidity out of range")
    return {"temperature_c": temperature / 100, "humidity_pct": humidity / 100}
