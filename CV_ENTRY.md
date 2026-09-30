# CV project entry

**LoRaWAN Remote Sensor Monitor** | Python, ChirpStack v4, MQTT, SQLite, Docker

- Built a Dockerized IoT monitoring system integrated with ChirpStack v4 and MQTT, with SQLite persistence, REST endpoints, online/stale device health tracking, malformed-payload handling, and historical telemetry charts.
- Validated an end-to-end **simulated LoRaWAN** workflow from OTAA device activation and simulated gateway traffic through ChirpStack application uplinks to the monitoring dashboard.
- Added automated tests covering payload decoding, ingestion, persistence, staleness, health state, and valid/invalid message handling.

**Scope note:** ChirpStack simulation is verified. Physical RF gateway/device and range testing are not claimed.

**GitHub:** https://github.com/faizankhan107/lorawan-iot-monitor
