"""MQTT subscriber, SQLite store, and small dependency-free HTTP dashboard."""

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import paho.mqtt.client as mqtt

from .ingest import parse_uplink


LOG = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "telemetry.db")
STALE_AFTER = int(os.getenv("STALE_AFTER_SECONDS", "90"))

HTML = Path(__file__).with_name("index.html").read_bytes()

STATE_LOCK = threading.Lock()

RUNTIME_STATE = {
    "mqtt_connected": False,
    "accepted_messages": 0,
    "rejected_messages": 0,
    "last_message_at": None,
}


def connect_db():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row

    db.execute("PRAGMA journal_mode=WAL")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at INTEGER NOT NULL,
            source TEXT NOT NULL,
            dev_eui TEXT NOT NULL,
            name TEXT NOT NULL,
            temperature_c REAL NOT NULL,
            humidity_pct REAL NOT NULL,
            rssi INTEGER,
            snr REAL
        )
        """
    )

    db.execute(
        """
        CREATE INDEX IF NOT EXISTS samples_device_time
        ON samples(dev_eui, received_at)
        """
    )

    db.commit()
    return db


def save_sample(sample):
    with closing(connect_db()) as db:
        db.execute(
            """
            INSERT INTO samples (
                received_at,
                source,
                dev_eui,
                name,
                temperature_c,
                humidity_pct,
                rssi,
                snr
            )
            VALUES (
                :received_at,
                :source,
                :dev_eui,
                :name,
                :temperature_c,
                :humidity_pct,
                :rssi,
                :snr
            )
            """,
            {
                "received_at": int(time.time()),
                **sample,
            },
        )

        db.commit()


def latest_samples(now=None):
    now = int(time.time()) if now is None else now

    with closing(connect_db()) as db:
        rows = db.execute(
            """
            SELECT s.*
            FROM samples s
            WHERE s.id = (
                SELECT MAX(x.id)
                FROM samples x
                WHERE x.dev_eui = s.dev_eui
            )
            ORDER BY s.name, s.dev_eui
            """
        ).fetchall()

    return [
        dict(row)
        | {
            "status": (
                "online"
                if now - row["received_at"] <= STALE_AFTER
                else "stale"
            )
        }
        for row in rows
    ]


def history(dev_eui, limit=60):
    with closing(connect_db()) as db:
        rows = db.execute(
            """
            SELECT
                received_at,
                temperature_c,
                humidity_pct,
                rssi,
                snr
            FROM samples
            WHERE dev_eui = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (dev_eui, limit),
        ).fetchall()

    return [dict(row) for row in reversed(rows)]


def database_status():
    try:
        with closing(connect_db()) as db:
            db.execute("SELECT 1").fetchone()

        return "ok"

    except sqlite3.Error as exc:
        LOG.warning(
            "Database health check failed: %s",
            exc,
        )

        return "error"


def health_snapshot():
    db_state = database_status()

    with STATE_LOCK:
        runtime = dict(RUNTIME_STATE)

    mqtt_state = (
        "connected"
        if runtime["mqtt_connected"]
        else "disconnected"
    )

    overall = (
        "ok"
        if (
            db_state == "ok"
            and runtime["mqtt_connected"]
        )
        else "degraded"
    )

    return {
        "status": overall,
        "database": db_state,
        "mqtt": mqtt_state,
        "accepted_messages": runtime["accepted_messages"],
        "rejected_messages": runtime["rejected_messages"],
        "last_message_at": runtime["last_message_at"],
    }


def process_message(topic, payload):
    """
    Validate, store, and account for one MQTT uplink.

    Returns True for a successfully accepted message.
    Returns False if the message is rejected.
    """

    try:
        sample = parse_uplink(
            topic,
            payload,
        )

        save_sample(sample)

        with STATE_LOCK:
            RUNTIME_STATE["accepted_messages"] += 1
            RUNTIME_STATE["last_message_at"] = int(time.time())

        return True

    except (
        ValueError,
        TypeError,
        sqlite3.Error,
    ) as exc:

        with STATE_LOCK:
            RUNTIME_STATE["rejected_messages"] += 1

        LOG.warning(
            "Skipped %s: %s",
            topic,
            exc,
        )

        return False


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        path = urlsplit(self.path).path

        if path == "/":
            self.respond(
                200,
                HTML,
                "text/html; charset=utf-8",
            )

        elif path == "/api/devices":
            self.respond_json(
                200,
                latest_samples(),
            )

        elif path.startswith("/api/history/"):
            dev_eui = path.removeprefix(
                "/api/history/"
            )

            if (
                len(dev_eui) != 16
                or any(
                    c not in "0123456789abcdefABCDEF"
                    for c in dev_eui
                )
            ):
                self.respond_json(
                    400,
                    {
                        "error": "invalid device EUI"
                    },
                )

            else:
                self.respond_json(
                    200,
                    history(
                        dev_eui.lower()
                    ),
                )

        elif path == "/health":
            health = health_snapshot()

            status_code = (
                200
                if health["status"] == "ok"
                else 503
            )

            self.respond_json(
                status_code,
                health,
            )

        else:
            self.respond_json(
                404,
                {
                    "error": "not found"
                },
            )

    def respond_json(
        self,
        status,
        data,
    ):
        self.respond(
            status,
            json.dumps(data).encode(),
            "application/json",
        )

    def respond(
        self,
        status,
        body,
        mime,
    ):
        self.send_response(status)

        self.send_header(
            "Content-Type",
            mime,
        )

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.end_headers()

        self.wfile.write(body)


def start_mqtt():
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="monitor-subscriber",
    )

    def on_connect(
        client,
        userdata,
        flags,
        reason_code,
        properties,
    ):
        if reason_code == 0:
            with STATE_LOCK:
                RUNTIME_STATE["mqtt_connected"] = True

            for topic in (
                "demo/+/device/+/event/up",
                "application/+/device/+/event/up",
            ):
                client.subscribe(
                    topic,
                    qos=1,
                )

            LOG.info(
                "MQTT connected; subscribed "
                "to demo and ChirpStack uplinks"
            )

        else:
            with STATE_LOCK:
                RUNTIME_STATE["mqtt_connected"] = False

            LOG.warning(
                "MQTT connection rejected: %s",
                reason_code,
            )

    def on_disconnect(
        client,
        userdata,
        disconnect_flags,
        reason_code,
        properties,
    ):
        with STATE_LOCK:
            RUNTIME_STATE["mqtt_connected"] = False

        LOG.warning(
            "MQTT disconnected: %s",
            reason_code,
        )

    def on_message(
        client,
        userdata,
        message,
    ):
        process_message(
            message.topic,
            message.payload,
        )

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    host = os.getenv(
        "MQTT_HOST",
        "localhost",
    )

    mqtt_port = int(
        os.getenv(
            "MQTT_PORT",
            "1883",
        )
    )

    def run():
        while True:
            try:
                client.connect(
                    host,
                    mqtt_port,
                    keepalive=60,
                )

                client.loop_forever(
                    retry_first_connection=True
                )

            except OSError as exc:
                with STATE_LOCK:
                    RUNTIME_STATE["mqtt_connected"] = False

                LOG.warning(
                    "MQTT unavailable (%s); "
                    "retrying in 3 seconds",
                    exc,
                )

                time.sleep(3)

    threading.Thread(
        target=run,
        name="mqtt",
        daemon=True,
    ).start()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(message)s"
        ),
    )

    Path(DB_PATH).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with closing(connect_db()):
        pass

    port = int(
        os.getenv(
            "PORT",
            "8000",
        )
    )

    # Important:
    # Bind HTTP port BEFORE starting MQTT.
    #
    # If another monitor instance is already using the dashboard port,
    # this process fails here and never connects to MQTT.
    # This prevents duplicate monitor instances from stealing the same
    # MQTT client session ("monitor-subscriber").
    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        Handler,
    )

    start_mqtt()

    LOG.info(
        "Dashboard listening on port %s",
        port,
    )

    server.serve_forever()


if __name__ == "__main__":
    main()
