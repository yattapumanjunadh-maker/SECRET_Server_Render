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

app = Flask(__name__, template_folder="templates", static_folder="static")
sock = Sock(app)

ADMIN_TOKEN = os.environ.get("SECRET_ADMIN_TOKEN", "change-me-admin-token")
DEVICE_TOKEN = os.environ.get("SECRET_DEVICE_TOKEN", "change-me-device-token")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

devices = {}
devices_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def admin_ok(req):
    return secrets.compare_digest(
        req.headers.get("X-Admin-Token", ""), ADMIN_TOKEN
    )


def device_ok(req):
    return secrets.compare_digest(
        req.headers.get("X-Device-Token", ""), DEVICE_TOKEN
    )


def db_ping():
    if not DATABASE_URL or psycopg is None:
        return False
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


@app.get("/")
def dashboard():
    return render_template("dashboard.html")


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "time": now_iso(),
        "database": db_ping(),
    })


@app.post("/api/register")
def register():
    if not device_ok(request):
        return jsonify({"error": "invalid device token"}), 401

    data = request.get_json(silent=True) or {}
    device_id = str(data.get("deviceId", "")).strip()
    name = str(data.get("name", "SECRET Android")).strip()

    if not device_id:
        return jsonify({"error": "deviceId is required"}), 400

    with devices_lock:
        item = devices.get(device_id, {})
        item.update({
            "deviceId": device_id,
            "name": name,
            "capabilities": data.get("capabilities", {}),
            "appVersion": data.get("appVersion", ""),
            "connected": item.get("connected", False),
            "lastSeen": now_iso(),
        })
        devices[device_id] = item

    return jsonify({"ok": True, "deviceId": device_id})


@app.get("/api/devices")
def list_devices():
    if not admin_ok(request):
        return jsonify({"error": "unauthorized"}), 401

    with devices_lock:
        result = []
        for item in devices.values():
            copy = dict(item)
            copy.pop("ws", None)
            result.append(copy)

    return jsonify(result)


@app.post("/api/command/<device_id>")
def send_command(device_id):
    if not admin_ok(request):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    command = str(data.get("command", "")).strip()

    allowed = {
        "ping",
        "get_status",
        "request_location",
        "request_camera",
        "request_microphone",
        "open_files",
    }

    if command not in allowed:
        return jsonify({"error": "unsupported command"}), 400

    with devices_lock:
        device = devices.get(device_id)
        if not device:
            return jsonify({"error": "device not registered"}), 404
        ws = device.get("ws")

    if ws is None:
        return jsonify({"error": "device is offline"}), 409

    payload = {
        "type": "command",
        "command": command,
        "requestId": secrets.token_hex(8),
        "time": now_iso(),
    }

    try:
        ws.send(json.dumps(payload))
        return jsonify({"ok": True, "requestId": payload["requestId"]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@sock.route("/ws/<device_id>")
def websocket(ws, device_id):
    token = request.args.get("token", "")

    if not secrets.compare_digest(token, DEVICE_TOKEN):
        ws.close()
        return

    with devices_lock:
        device = devices.setdefault(device_id, {
            "deviceId": device_id,
            "name": "SECRET Android",
            "capabilities": {},
        })
        device["connected"] = True
        device["lastSeen"] = now_iso()
        device["ws"] = ws

    try:
        ws.send(json.dumps({
            "type": "connected",
            "message": "SECRET server connection established",
            "time": now_iso(),
        }))

        while True:
            raw = ws.receive()
            if raw is None:
                break

            try:
                msg = json.loads(raw)
            except Exception:
                msg = {"type": "text", "value": raw}

            with devices_lock:
                device = devices.get(device_id)
                if device:
                    device["lastSeen"] = now_iso()

                    if msg.get("type") == "hello":
                        device["name"] = msg.get("name", device["name"])
                        device["capabilities"] = msg.get(
                            "capabilities", device["capabilities"]
                        )
                        device["appVersion"] = msg.get(
                            "appVersion", device.get("appVersion", "")
                        )

    except Exception:
        pass
    finally:
        with devices_lock:
            device = devices.get(device_id)
            if device:
                device["connected"] = False
                device["ws"] = None
                device["lastSeen"] = now_iso()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
