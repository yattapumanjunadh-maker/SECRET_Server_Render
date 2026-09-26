# SECRET Server — Render

## Local run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

set SECRET_ADMIN_TOKEN=replace-with-admin-secret
set SECRET_DEVICE_TOKEN=replace-with-device-secret

python app.py
```

Open `http://127.0.0.1:5000`.

## Render

Create a Render Web Service from this folder/repository. Set:

- `SECRET_ADMIN_TOKEN`
- `SECRET_DEVICE_TOKEN`
- `DATABASE_URL` (optional for the starter health check)

The Render service gives the Android app a stable HTTPS/WSS host such as:

`https://secret-control-server.onrender.com`

The WebSocket endpoint is:

`wss://secret-control-server.onrender.com/ws/<deviceId>?token=...`

## Important

This starter keeps the live device registry in memory. PostgreSQL is prepared as the persistence layer, but database models/migrations are intentionally left for the next step.

Sensitive device capabilities must remain user-authorized and visible. This project does not implement covert camera, microphone, location, or file collection.
