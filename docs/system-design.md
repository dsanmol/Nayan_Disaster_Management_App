# Nayan system design

## Problem statement

During disasters, emergency information is often fragmented across citizens, responders, and other sources. This can make it difficult to prioritize reports and coordinate response capacity. Nayan centralizes incident reports, triage, resources, missions, shelter capacity, and public safety messages in a shared operations application.

## Roles

| Capability | Citizen | Responder | Admin |
| --- | --- | --- | --- |
| Register, submit incident, view alerts and shelters | Yes | Yes | Yes |
| View district incident list | Active incidents | District incidents | District incidents |
| View mission and update field progress | No | Yes | Yes |
| Manage response resources and shelters | No | No | Yes |
| Issue public alerts and assign teams | No | No | Yes |
| Review operational analytics | No | Limited | Yes |

Public signup creates citizen accounts. Admin-created staff accounts use the protected admin user API. The responder demo account currently represents the responder role; production deployments still need explicit responder-to-resource identity assignments.

## Architecture

```text
Browser (HTML / CSS / JavaScript)
  |  Bearer-token REST API + WebSocket event channel
FastAPI service
  |-- auth and role checks
  |-- incident, resource, mission, shelter and alert APIs
  |-- local text classification and similarity, severity, priority, recommendations
  |-- simulator, analytics, image upload, route adapter
PostgreSQL + PostGIS (deploy) / SQLite (local)
  |-- incidents, users, resources, missions, shelters, alerts, history
OpenStreetMap tiles / optional private OSRM route service
```

The app is served at `/`; OpenAPI documentation is available at `/docs`. `DATABASE_URL` selects the SQLAlchemy database. On PostgreSQL, startup creates PostGIS point columns and GiST spatial indexes, kept in sync with latitude and longitude columns. Local SQLite keeps the same coordinates without the PostGIS extension.

## Data model

- `users`: name, unique email, password hash, role, phone, creation time.
- `incidents`: type, description, place, coordinates, people affected, medical need, severity score/band, status, resource label, reporter, timestamps, PostGIS point when PostgreSQL is used.
- `incident_updates`: actor, incident, status, note, and timestamp for the incident history.
- `resources`: type, base, coordinates, availability state, capacity, and skills.
- `missions`: incident/resource links, assigning staff user, status, assignment and completion times.
- `shelters`: location, capacity, occupancy, and status.
- `alerts`: title, message, severity, area, optional incident and creation/expiry times.

## Decision-support algorithms

### Severity score

Start at 20; add up to 35 points for people affected, 23 for medical need, 18 for high-risk keywords (flood, fire, collapse, chemical), and 9 for urgency language; cap at 99. Bands are Critical (80+), High (65-79), Medium (40-64), and Low (below 40). This is an illustrative heuristic, not a validated emergency model. A human operator remains responsible for triage and override.

### Priority queue

Incidents are ordered by descending score and then newest report. Production prioritization should consider waiting time, exposure, confidence, incident progression, and resource constraints, with displayed rationale and a recorded operator decision.

### Duplicate suggestions

Open reports within one kilometer are compared using TF-IDF cosine similarity over the incident type and description. Classification uses a local TF-IDF + Logistic Regression pipeline trained on the small curated prototype corpus in `backend/app/ml.py`. Both outputs are advisory: the corpus is not validated for live response, confidence is not calibrated, and an operator must review classifications and possible duplicates.

### Resource recommendation

Available resources are filtered and ranked with a capability/type match and straight-line distance. The admin dashboard dispatches the highest-ranked candidate. Operational deployments should use skills, capacity, real travel time, access constraints, and human approval.

### Routes

When `NAYAN_ROUTING_URL` points to a private OSRM-compatible service, the route endpoint requests driving geometry and estimated travel time from that service. No external routing host is called by default. When unset or unreachable, the endpoint returns an explicitly labeled straight-line estimate. Map display uses OpenStreetMap tiles and requires an Internet connection.

## Live and simulation behavior

A WebSocket endpoint emits incident and mission changes to connected authenticated clients. The admin dashboard can generate a sample report every ten seconds until simulation is stopped. Seed data is illustrative and centered on Indore; do not treat it as real incidents.

## Deployment

The included Render Blueprint creates a free web service and free PostgreSQL database for a short-lived demo. Free Render Postgres expires after 30 days. `docker-compose.yml` is the more durable local/self-hosted path and uses a persistent PostGIS volume. Production deployments need durable PostgreSQL and object storage for images, TLS, generated secret values, backups, migrations, rate limits, monitoring, and a reviewed privacy/access policy.

## Boundaries

Nayan has not been validated with emergency agencies and must not be used for real dispatch or public warning decisions. Staff identity mapping, email verification/reset, user-facing audit history, durable image storage, PostGIS query optimization, data retention, integration with authoritative hazard data, and formal algorithm validation remain future implementation work.
