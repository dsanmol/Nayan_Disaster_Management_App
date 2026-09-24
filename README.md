# Nayan Disaster Management System

Nayan is a browser-based disaster response coordination application for the Indore district demo scenario. A FastAPI service serves the UI and JSON API; SQLite is the default local database and PostgreSQL with PostGIS is supported for deployment.

## Run locally on Windows

From this `outputs` folder:

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
$env:NAYAN_JWT_SECRET = 'replace-this-with-a-long-random-local-secret'
backend/.venv/Scripts/python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. The first run creates and seeds the database at `backend/nayan.db`. API documentation is at http://127.0.0.1:8000/docs. On macOS/Linux use `backend/.venv/bin/python` for the venv executable.

## Demo accounts

- Admin: `admin@nayan.demo` / `AdminDemo123`
- Responder: `responder@nayan.demo` / `Responder123`
- Citizen: `citizen@nayan.demo` / `CitizenDemo123`

New public registrations receive the Citizen role. Administrators can create staff accounts through `POST /api/admin/users`. Change demo passwords and set a unique `NAYAN_JWT_SECRET` before exposing any instance publicly.

## Included workflows

- Citizen registration/login, incident intake, map-picked coordinates, optional photo uploads, incident status view, public alerts, and shelters.
- Responder sign-in, mission tracking, field status progression, and route previews.
- Admin command dashboard, incident priority queue, resource/shelter/alert management, assignments, analytics, and recurring 10-second incident simulation.
- Local TF-IDF + Logistic Regression incident classification, TF-IDF cosine similarity for nearby duplicate suggestions, transparent severity scoring, resource recommendations, WebSocket updates, CSV export, and an OpenStreetMap view.
- SQLite for local setup. PostgreSQL deployments automatically enable PostGIS and maintain indexed point geometry for incidents, resources, and shelters.

Incident classification, severity, duplicate and resource results are decision-support suggestions and require human review. The classifier is trained on a small set of curated prototype examples included in `backend/app/ml.py`; it has not been validated for emergency operations and must not be used as a substitute for dispatcher judgment. Model confidence is not a calibrated probability. Without a configured `NAYAN_ROUTING_URL`, routes are straight-line estimates. To use a private OSRM service, set that URL on the API server. Browser map tiles are provided by OpenStreetMap; review their usage policy before high-volume or operational use.

## Docker with PostGIS

Set `NAYAN_JWT_SECRET` in your shell, then run:

```powershell
docker compose up --build
```

The compose stack starts the web app and a PostGIS database with a persistent Docker volume.

## Render demo deployment

`render.yaml` defines a free web service and free managed PostgreSQL database. Push this `outputs` directory to a Git provider repository and create a Render Blueprint from it. Render generates a private JWT secret and initial admin password; sign in as `admin@nayan.demo` and read the generated password from the service environment settings. Shared demo passwords are disabled on Render. Free Render Postgres expires after 30 days, so this blueprint is for a temporary demo only; select a durable paid database for ongoing use. Render free web services can sleep and do not include persistent local disk storage, so uploaded photos are temporary. Configure object storage before relying on image retention.

## Production work still required

This is a student/demo project, not an emergency-service system. Before real operational use, perform security review and load testing; set up durable backup and image storage, account verification and password reset, incident access rules for citizen privacy, staff-to-resource identity mapping, audited human-approved workflows, validated emergency algorithms and GIS data, observability, and a documented recovery plan. Verify map-tile and routing service agreements for the expected traffic and data sensitivity. Do not use its recommendations as autonomous dispatch decisions.
