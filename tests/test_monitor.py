import json
import os
import tempfile
import unittest
from unittest.mock import patch

from monitor.codec import (
    decode_sensor,
    encode_sensor,
)

from monitor.ingest import parse_uplink


class CodecTests(unittest.TestCase):

    def test_round_trip_and_negative_temperature(self):
        self.assertEqual(
            decode_sensor(
                encode_sensor(
                    -4.25,
                    67.5,
                )
            ),
            {
                "temperature_c": -4.25,
                "humidity_pct": 67.5,
            },
        )

    def test_reject_bad_payload(self):
        for payload in (
            "!",
            "YQ==",
        ):
            with (
                self.subTest(
                    payload=payload
                ),
                self.assertRaises(
                    ValueError
                ),
            ):
                decode_sensor(payload)


class IngestTests(unittest.TestCase):

    def setUp(self):
        self.eui = (
            "a84041aabbccdd01"
        )

        self.event = {
            "deviceInfo": {
                "devEui":
                    self.eui,
                "deviceName":
                    "Lab sensor",
            },
            "data":
                encode_sensor(
                    23.45,
                    60.5,
                ),
            "rxInfo": [
                {
                    "rssi": -75,
                    "snr": 6.5,
                }
            ],
        }

    def test_real_and_simulated_events(self):
        for prefix, source in (
            (
                "application/abc",
                "chirpstack",
            ),
            (
                "demo/local",
                "simulated",
            ),
        ):
            with self.subTest(
                prefix=prefix
            ):
                row = parse_uplink(
                    (
                        f"{prefix}/device/"
                        f"{self.eui}/event/up"
                    ),
                    json.dumps(
                        self.event
                    ).encode(),
                )

                self.assertEqual(
                    (
                        row["source"],
                        row["temperature_c"],
                        row["rssi"],
                    ),
                    (
                        source,
                        23.45,
                        -75,
                    ),
                )

    def test_reject_eui_mismatch(self):
        self.event[
            "deviceInfo"
        ][
            "devEui"
        ] = (
            "0000000000000000"
        )

        with self.assertRaisesRegex(
            ValueError,
            "does not match",
        ):
            parse_uplink(
                (
                    "demo/local/device/"
                    f"{self.eui}/event/up"
                ),
                json.dumps(
                    self.event
                ).encode(),
            )

    def test_reject_unknown_topic(self):
        with self.assertRaisesRegex(
            ValueError,
            "unsupported topic",
        ):
            parse_uplink(
                "anything/else",
                json.dumps(
                    self.event
                ).encode(),
            )


class StorageTests(unittest.TestCase):

    def test_persist_and_stale_transition(self):
        with tempfile.TemporaryDirectory() as directory:

            with patch.dict(
                os.environ,
                {
                    "DB_PATH":
                        directory
                        + "/data.db"
                },
            ):
                import monitor.app as app

                previous = app.DB_PATH

                app.DB_PATH = (
                    os.environ[
                        "DB_PATH"
                    ]
                )

                try:
                    row = parse_uplink(
                        (
                            "demo/local/device/"
                            "a84041aabbccdd01/"
                            "event/up"
                        ),
                        json.dumps(
                            {
                                "deviceInfo": {
                                    "devEui":
                                        "a84041aabbccdd01"
                                },
                                "data":
                                    encode_sensor(
                                        22,
                                        50,
                                    ),
                            }
                        ).encode(),
                    )

                    with patch.object(
                        app.time,
                        "time",
                        return_value=1000,
                    ):
                        app.save_sample(row)

                    self.assertEqual(
                        app.latest_samples(
                            now=1001
                        )[0]["status"],
                        "online",
                    )

                    self.assertEqual(
                        app.latest_samples(
                            now=1090
                        )[0]["status"],
                        "online",
                    )

                    self.assertEqual(
                        app.latest_samples(
                            now=1091
                        )[0]["status"],
                        "stale",
                    )

                    self.assertEqual(
                        len(
                            app.history(
                                "a84041aabbccdd01"
                            )
                        ),
                        1,
                    )

                finally:
                    app.DB_PATH = previous


class ReliabilityTests(unittest.TestCase):

    def setUp(self):
        import monitor.app as app

        self.app = app

        self.previous_state = dict(
            app.RUNTIME_STATE
        )

        with app.STATE_LOCK:
            app.RUNTIME_STATE.update(
                {
                    "mqtt_connected":
                        False,
                    "accepted_messages":
                        0,
                    "rejected_messages":
                        0,
                    "last_message_at":
                        None,
                }
            )

    def tearDown(self):
        with self.app.STATE_LOCK:
            self.app.RUNTIME_STATE.clear()

            self.app.RUNTIME_STATE.update(
                self.previous_state
            )

    def test_health_reports_mqtt_state(self):

        with patch.object(
            self.app,
            "database_status",
            return_value="ok",
        ):
            health = (
                self.app
                .health_snapshot()
            )

            self.assertEqual(
                health["status"],
                "degraded",
            )

            self.assertEqual(
                health["mqtt"],
                "disconnected",
            )

            with self.app.STATE_LOCK:
                self.app.RUNTIME_STATE[
                    "mqtt_connected"
                ] = True

            health = (
                self.app
                .health_snapshot()
            )

            self.assertEqual(
                health["status"],
                "ok",
            )

            self.assertEqual(
                health["mqtt"],
                "connected",
            )

    def test_invalid_message_is_rejected_without_exception(
        self
    ):
        result = (
            self.app.process_message(
                (
                    "demo/local/device/"
                    "a84041aabbccdd01/"
                    "event/up"
                ),
                b"{bad-json",
            )
        )

        self.assertFalse(result)

        with self.app.STATE_LOCK:
            self.assertEqual(
                self.app.RUNTIME_STATE[
                    "accepted_messages"
                ],
                0,
            )

            self.assertEqual(
                self.app.RUNTIME_STATE[
                    "rejected_messages"
                ],
                1,
            )

    def test_valid_message_is_accepted_and_stored(
        self
    ):
        with tempfile.TemporaryDirectory() as directory:

            previous_db = (
                self.app.DB_PATH
            )

            self.app.DB_PATH = (
                directory
                + "/reliability.db"
            )

            try:
                event = {
                    "deviceInfo": {
                        "devEui":
                            "a84041aabbccdd01",
                        "deviceName":
                            "Reliability sensor",
                    },
                    "data":
                        encode_sensor(
                            24.5,
                            55.0,
                        ),
                    "rxInfo": [
                        {
                            "rssi": -72,
                            "snr": 7.0,
                        }
                    ],
                }

                result = (
                    self.app
                    .process_message(
                        (
                            "demo/local/device/"
                            "a84041aabbccdd01/"
                            "event/up"
                        ),
                        json.dumps(
                            event
                        ).encode(),
                    )
                )

                self.assertTrue(
                    result
                )

                with self.app.STATE_LOCK:
                    self.assertEqual(
                        self.app.RUNTIME_STATE[
                            "accepted_messages"
                        ],
                        1,
                    )

                    self.assertEqual(
                        self.app.RUNTIME_STATE[
                            "rejected_messages"
                        ],
                        0,
                    )

                rows = (
                    self.app.history(
                        "a84041aabbccdd01"
                    )
                )

                self.assertEqual(
                    len(rows),
                    1,
                )

            finally:
                self.app.DB_PATH = (
                    previous_db
                )


if __name__ == "__main__":
    unittest.main()
