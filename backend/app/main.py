from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .algorithms import duplicate_candidates, haversine_km, recommend_resources

APP_DIR = Path(__file__).resolve().parent
OUTPUTS = APP_DIR.parents[1]
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(APP_DIR.parent / 'nayan.db').as_posix()}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://"):]
JWT_SECRET = os.getenv("NAYAN_JWT_SECRET", "local-demo-secret-change-before-deployment")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}, pool_pre_ping=True)
Session = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(250), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="CITIZEN")
    phone: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    type: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str] = mapped_column(Text)
    place: Mapped[str] = mapped_column(String(240))
    latitude: Mapped[float] = mapped_column(Float, default=22.7196)
    longitude: Mapped[float] = mapped_column(Float, default=75.8577)
    people_affected: Mapped[int] = mapped_column(Integer, default=0)
    medical_required: Mapped[bool] = mapped_column(Boolean, default=False)
    severity_score: Mapped[int] = mapped_column(Integer, default=20)
    severity_level: Mapped[str] = mapped_column(String(20), default="Low", index=True)
    status: Mapped[str] = mapped_column(String(30), default="Reported", index=True)
    unit: Mapped[str] = mapped_column(String(140), default="Unassigned")
    reporter_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Resource(Base):
    __tablename__ = "resources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    type: Mapped[str] = mapped_column(String(60))
    base: Mapped[str] = mapped_column(String(160), default="Indore district")
    latitude: Mapped[float] = mapped_column(Float, default=22.7196)
    longitude: Mapped[float] = mapped_column(Float, default=75.8577)
    status: Mapped[str] = mapped_column(String(30), default="Available", index=True)
    capacity: Mapped[str] = mapped_column(String(120), default="")
    skills: Mapped[str] = mapped_column(Text, default="")


class Mission(Base):
    __tablename__ = "missions"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"))
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="Assigned")
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Shelter(Base):
    __tablename__ = "shelters"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    place: Mapped[str] = mapped_column(String(160))
    latitude: Mapped[float] = mapped_column(Float, default=22.7196)
    longitude: Mapped[float] = mapped_column(Float, default=75.8577)
    capacity: Mapped[int] = mapped_column(Integer)
    occupied: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="Open")


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(220))
    message: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="Info")
    area: Mapped[str] = mapped_column(String(160), default="District-wide")
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IncidentUpdate(Base):
    __tablename__ = "incident_updates"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return base64.b64encode(salt + digest).decode()


def verify_password(password: str, saved: str) -> bool:
    try:
        raw = base64.b64decode(saved)
        return hmac.compare_digest(raw[16:], hashlib.pbkdf2_hmac("sha256", password.encode(), raw[:16], 310_000))
    except (ValueError, TypeError):
        return False


def token_for(user: User) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": user.id, "role": user.role, "exp": int(time.time()) + 86400}).encode()).decode().rstrip("=")
    body = f"{header}.{payload}"
    sig = base64.urlsafe_b64encode(hmac.new(JWT_SECRET.encode(), body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{body}.{sig}"


def decode_token(token: str) -> dict[str, Any]:
    try:
        head, body, signature = token.split(".")
        expected = base64.urlsafe_b64encode(hmac.new(JWT_SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()).decode().rstrip("=")
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload["exp"] < time.time():
            raise ValueError("expired")
        return payload
    except Exception as exc:
        raise HTTPException(401, "Invalid or expired session") from exc


def current_user(authorization: str | None = None) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in to continue")
    payload = decode_token(authorization[7:])
    with Session() as db:
        user = db.get(User, payload["sub"])
        if not user:
            raise HTTPException(401, "Account not found")
        db.expunge(user)
        return user


from fastapi import Header


def user_dep(authorization: str | None = Header(default=None)) -> User:
    return current_user(authorization)


def allow(*roles: str):
    def check(user: User = Depends(user_dep)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Your account role cannot perform this action")
        return user
    return check


def score_incident(people: int, medical: bool, kind: str, text: str) -> tuple[int, str]:
    corpus = f"{kind} {text}".lower()
    score = 20 + min(max(people, 0), 100) * 0.35 + (23 if medical else 0)
    score += 18 if any(word in corpus for word in ("flood", "fire", "collapse", "chemical")) else 0
    score += 9 if any(word in corpus for word in ("trapped", "urgent", "severe", "critical")) else 0
    score = min(99, round(score))
    level = "Critical" if score >= 80 else "High" if score >= 65 else "Medium" if score >= 40 else "Low"
    return score, level


def as_incident(i: Incident) -> dict[str, Any]:
    return {"id": i.id, "type": i.type, "description": i.description, "place": i.place, "latitude": i.latitude, "longitude": i.longitude, "people_affected": i.people_affected, "medical_required": i.medical_required, "severity_score": i.severity_score, "severity_level": i.severity_level, "status": i.status, "unit": i.unit, "created_at": i.created_at.isoformat()}


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=250)
    password: str = Field(min_length=10, max_length=128)
    phone: str = ""


class LoginIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=250)
    password: str


class IncidentIn(BaseModel):
    type: str = Field(min_length=2, max_length=100)
    description: str = Field(min_length=5, max_length=4000)
    place: str = Field(min_length=2, max_length=240)
    latitude: float = Field(ge=-90, le=90, default=22.7196)
    longitude: float = Field(ge=-180, le=180, default=75.8577)
    people_affected: int = Field(ge=0, le=100000, default=0)
    medical_required: bool = False


class StatusIn(BaseModel):
    status: str
    note: str = ""


class ResourceIn(BaseModel):
    name: str
    type: str
    base: str = "Indore district"
    latitude: float = 22.7196
    longitude: float = 75.8577
    capacity: str = ""
    skills: str = ""
    status: str = "Available"


class ShelterIn(BaseModel):
    name: str
    place: str
    latitude: float = 22.7196
    longitude: float = 75.8577
    capacity: int = Field(gt=0)
    occupied: int = Field(ge=0, default=0)
    status: str = "Open"


class AlertIn(BaseModel):
    title: str
    message: str
    severity: str = "Info"
    area: str = "District-wide"
    incident_id: str | None = None


class AssignmentIn(BaseModel):
    incident_id: str
    resource_id: int


class ClassifyIn(BaseModel):
    text: str = Field(min_length=3, max_length=4000)


app = FastAPI(title="Nayan Disaster Management System", version="1.0.0", description="Incident coordination API for the Nayan response platform")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("NAYAN_CORS_ORIGINS", "*").split(","), allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
subscribers: set[WebSocket] = set()


async def broadcast(event: dict[str, Any]):
    dead = []
    for socket in subscribers:
        try:
            await socket.send_json(event)
        except Exception:
            dead.append(socket)
    for socket in dead:
        subscribers.discard(socket)


def init_db():
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            for table in ("incidents", "resources", "shelters"):
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS geom geometry(Point, 4326)"))
                connection.execute(text(f"""
                    CREATE OR REPLACE FUNCTION nayan_sync_{table}_geom() RETURNS trigger AS $$
                    BEGIN
                        NEW.geom := ST_SetSRID(ST_MakePoint(NEW.longitude, NEW.latitude), 4326);
                        RETURN NEW;
                    END;
                    $$ LANGUAGE plpgsql
                """))
                connection.execute(text(f"DROP TRIGGER IF EXISTS nayan_sync_{table}_geom_trigger ON {table}"))
                connection.execute(text(f"CREATE TRIGGER nayan_sync_{table}_geom_trigger BEFORE INSERT OR UPDATE OF latitude, longitude ON {table} FOR EACH ROW EXECUTE FUNCTION nayan_sync_{table}_geom()"))
                connection.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{table}_geom ON {table} USING GIST (geom)"))
    with Session.begin() as db:
        if db.scalar(select(Incident.id).limit(1)):
            return
        if os.getenv("NAYAN_DEMO_MODE", "true").lower() == "true":
            accounts = [("Asha Citizen", "citizen@nayan.demo", "CitizenDemo123", "CITIZEN"), ("Ravi Responder", "responder@nayan.demo", "Responder123", "RESPONDER"), ("Arjun Sharma", "admin@nayan.demo", "AdminDemo123", "ADMIN")]
            users = [User(name=n, email=e, password_hash=password_hash(p), role=r) for n, e, p, r in accounts]
            db.add_all(users)
        else:
            email = os.getenv("NAYAN_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
            password = os.getenv("NAYAN_BOOTSTRAP_ADMIN_PASSWORD", "")
            users = []
            if email and len(password) >= 16:
                users.append(User(name=os.getenv("NAYAN_BOOTSTRAP_ADMIN_NAME", "Nayan Administrator"), email=email, password_hash=password_hash(password), role="ADMIN"))
                db.add(users[0])
        db.flush()
        citizen = next((u for u in users if u.role == "CITIZEN"), None)
        admin = next((u for u in users if u.role == "ADMIN"), None)
        items = [
            ("NAY-0261", "Urban flooding", "Water entering ground-floor homes near the main junction.", "Vijay Nagar · Scheme 54", 22.7532, 75.8937, 28, True, 94, "Critical", "En route", "Rescue Team Alpha"),
            ("NAY-0260", "Building fire", "Smoke reported from a commercial building; occupants evacuating.", "Rajwada · Maharaja Tukoji Road", 22.7196, 75.8577, 16, True, 86, "Critical", "Assigned", "Fire Unit 03"),
            ("NAY-0259", "Road accident", "Multi-vehicle collision blocking the northbound lane.", "AB Road · LIG Square", 22.7337, 75.8932, 7, True, 72, "High", "En route", "Ambulance 12"),
            ("NAY-0258", "Water supply disruption", "Water main break affecting nearby residential blocks.", "Rau · CAT Road", 22.6407, 75.8081, 140, False, 48, "Medium", "Reported", "Unassigned"),
        ]
        db.add_all([Incident(id=x[0], type=x[1], description=x[2], place=x[3], latitude=x[4], longitude=x[5], people_affected=x[6], medical_required=x[7], severity_score=x[8], severity_level=x[9], status=x[10], unit=x[11], reporter_id=citizen.id if citizen else None) for x in items])
        resources = [Resource(name="Ambulance 12", type="Ambulance", base="Central Fire Station", status="En route", capacity="2 patients", skills="Advanced life support"), Resource(name="Fire Unit 03", type="Fire team", base="MG Road Fire Station", status="Assigned", capacity="6 crew", skills="Urban fire response"), Resource(name="Rescue Team Alpha", type="Rescue team", base="Vijay Nagar HQ", status="En route", capacity="8 crew", skills="Water rescue · extraction"), Resource(name="Ambulance 08", type="Ambulance", base="MY Hospital", status="Available", capacity="2 patients", skills="Emergency medical"), Resource(name="Rescue Team Bravo", type="Rescue team", base="Rau staging area", status="Available", capacity="10 crew", skills="Search and rescue"), Resource(name="Fire Unit 01", type="Fire team", base="Palasia Station", status="Available", capacity="5 crew", skills="Fire suppression")]
        db.add_all(resources)
        db.flush()
        db.add_all([Shelter(name="Devi Ahilya Relief Center", place="Vijay Nagar", capacity=240, occupied=168), Shelter(name="Nehru Stadium Community Hall", place="South Tukoganj", capacity=350, occupied=291), Shelter(name="Rau Government School", place="Rau", capacity=160, occupied=92), Shelter(name="Scheme 78 Community Center", place="Vijay Nagar", capacity=180, occupied=104)])
        if admin:
            db.add_all([Mission(id="MSN-091", incident_id="NAY-0261", resource_id=resources[2].id, assigned_by=admin.id, status="En route"), Mission(id="MSN-090", incident_id="NAY-0260", resource_id=resources[1].id, assigned_by=admin.id, status="Assigned"), Mission(id="MSN-089", incident_id="NAY-0259", resource_id=resources[0].id, assigned_by=admin.id, status="En route")])
        db.add_all([Alert(title="Flood advisory · Khan River low-lying areas", message="Residents near the river corridor should move to higher ground and follow marked evacuation routes. Relief centers are open.", severity="Critical", area="Khan River corridor"), Alert(title="Traffic diversion · AB Road northbound", message="Emergency vehicles have priority at LIG Square. Use Ring Road as an alternate route where possible.", severity="High", area="AB Road")])


@app.on_event("startup")
def startup():
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok", "application": "Nayan Disaster Management System", "database": "connected"}


@app.get("/api/config")
def public_config():
    return {"demo_mode": os.getenv("NAYAN_DEMO_MODE", "true").lower() == "true"}


@app.post("/api/auth/register", status_code=201)
def register(payload: RegisterIn):
    email = payload.email.strip().lower()
    with Session.begin() as db:
        if db.scalar(select(User.id).where(User.email == email)):
            raise HTTPException(409, "An account already uses this email")
        user = User(name=payload.name, email=email, password_hash=password_hash(payload.password), phone=payload.phone, role="CITIZEN")
        db.add(user)
        db.flush()
        return {"access_token": token_for(user), "token_type": "bearer", "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role}}


@app.post("/api/auth/login")
def login(payload: LoginIn):
    with Session() as db:
        user = db.scalar(select(User).where(User.email == payload.email.strip().lower()))
        if not user or not verify_password(payload.password, user.password_hash):
            raise HTTPException(401, "Email or password is incorrect")
        return {"access_token": token_for(user), "token_type": "bearer", "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role}}


@app.post("/api/admin/users", status_code=201)
def create_staff_account(payload: RegisterIn, role: str = "RESPONDER", user: User = Depends(allow("ADMIN"))):
    role = role.upper()
    if role not in {"ADMIN", "RESPONDER"}:
        raise HTTPException(422, "Staff role must be ADMIN or RESPONDER")
    email = payload.email.strip().lower()
    with Session.begin() as db:
        if db.scalar(select(User.id).where(User.email == email)):
            raise HTTPException(409, "An account already uses this email")
        row = User(name=payload.name, email=email, password_hash=password_hash(payload.password), phone=payload.phone, role=role)
        db.add(row)
        db.flush()
        return {"id": row.id, "name": row.name, "email": row.email, "role": row.role}


@app.get("/api/admin/users")
def list_staff_accounts(user: User = Depends(allow("ADMIN"))):
    with Session() as db:
        return [{"id": row.id, "name": row.name, "email": row.email, "phone": row.phone, "role": row.role} for row in db.scalars(select(User).order_by(User.name)).all()]


@app.get("/api/auth/me")
def me(user: User = Depends(user_dep)):
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "phone": user.phone}


@app.get("/api/incidents")
def list_incidents(user: User = Depends(user_dep)):
    with Session() as db:
        query = select(Incident).order_by(Incident.severity_score.desc(), Incident.created_at.desc())
        if user.role == "CITIZEN":
            query = query.where(Incident.status.not_in({"Resolved", "Cancelled"}))
        return [as_incident(i) for i in db.scalars(query).all()]


@app.post("/api/incidents", status_code=201)
async def create_incident(payload: IncidentIn, user: User = Depends(user_dep)):
    score, severity = score_incident(payload.people_affected, payload.medical_required, payload.type, payload.description)
    with Session.begin() as db:
        count = db.scalar(select(Incident.id).order_by(Incident.id.desc()).limit(1))
        num = int(count.split("-")[-1]) + 1 if count and count.startswith("NAY-") else 1
        incident = Incident(id=f"NAY-{num:04d}", type=payload.type, description=payload.description, place=payload.place, latitude=payload.latitude, longitude=payload.longitude, people_affected=payload.people_affected, medical_required=payload.medical_required, severity_score=score, severity_level=severity, reporter_id=user.id)
        db.add(incident)
        db.flush()
        db.add(IncidentUpdate(incident_id=incident.id, actor_id=user.id, status="Reported", note="Incident submitted"))
        result = as_incident(incident)
    await broadcast({"type": "incident.created", "incident": result})
    return result


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str, user: User = Depends(user_dep)):
    with Session() as db:
        item = db.get(Incident, incident_id)
        if not item or user.role == "CITIZEN" and item.reporter_id != user.id and item.status in {"Resolved", "Cancelled"}:
            raise HTTPException(404, "Incident not found")
        return as_incident(item)


@app.patch("/api/incidents/{incident_id}/status")
async def update_incident(incident_id: str, payload: StatusIn, user: User = Depends(allow("ADMIN", "RESPONDER"))):
    if payload.status not in {"Reported", "Assigned", "En route", "Arrived", "Resolved", "Cancelled"}:
        raise HTTPException(422, "Unsupported incident status")
    with Session.begin() as db:
        item = db.get(Incident, incident_id)
        if not item:
            raise HTTPException(404, "Incident not found")
        item.status, item.updated_at = payload.status, datetime.now(timezone.utc)
        db.add(IncidentUpdate(incident_id=item.id, actor_id=user.id, status=payload.status, note=payload.note))
        result = as_incident(item)
    await broadcast({"type": "incident.updated", "incident": result})
    return result


@app.delete("/api/incidents/{incident_id}", status_code=204)
def delete_incident(incident_id: str, user: User = Depends(allow("ADMIN"))):
    with Session.begin() as db:
        item = db.get(Incident, incident_id)
        if not item:
            raise HTTPException(404, "Incident not found")
        item.status, item.updated_at = "Cancelled", datetime.now(timezone.utc)
        db.add(IncidentUpdate(incident_id=item.id, actor_id=user.id, status="Cancelled", note="Incident cancelled by administrator"))


@app.get("/api/resources")
def list_resources(user: User = Depends(user_dep)):
    with Session() as db:
        return [{"id": r.id, "name": r.name, "type": r.type, "base": r.base, "latitude": r.latitude, "longitude": r.longitude, "status": r.status, "capacity": r.capacity, "skills": r.skills} for r in db.scalars(select(Resource)).all()]


@app.post("/api/resources", status_code=201)
def create_resource(payload: ResourceIn, user: User = Depends(allow("ADMIN"))):
    with Session.begin() as db:
        row = Resource(**payload.model_dump())
        db.add(row)
        db.flush()
        return {"id": row.id, **payload.model_dump()}


@app.patch("/api/resources/{resource_id}")
def update_resource(resource_id: int, payload: dict[str, Any], user: User = Depends(allow("ADMIN"))):
    with Session.begin() as db:
        row = db.get(Resource, resource_id)
        if not row:
            raise HTTPException(404, "Resource not found")
        for key, value in payload.items():
            if key in {"name", "type", "base", "latitude", "longitude", "status", "capacity", "skills"}:
                setattr(row, key, value)
        return {"id": row.id, "name": row.name, "type": row.type, "base": row.base, "status": row.status, "capacity": row.capacity, "skills": row.skills}


@app.delete("/api/resources/{resource_id}", status_code=204)
def delete_resource(resource_id: int, user: User = Depends(allow("ADMIN"))):
    with Session.begin() as db:
        row = db.get(Resource, resource_id)
        if not row:
            raise HTTPException(404, "Resource not found")
        db.delete(row)


@app.get("/api/shelters")
def list_shelters(user: User = Depends(user_dep)):
    with Session() as db:
        return [{"id": s.id, "name": s.name, "place": s.place, "latitude": s.latitude, "longitude": s.longitude, "capacity": s.capacity, "occupied": s.occupied, "status": s.status} for s in db.scalars(select(Shelter)).all()]


@app.post("/api/shelters", status_code=201)
def create_shelter(payload: ShelterIn, user: User = Depends(allow("ADMIN"))):
    if payload.occupied > payload.capacity:
        raise HTTPException(422, "Occupancy cannot exceed capacity")
    with Session.begin() as db:
        row = Shelter(**payload.model_dump())
        db.add(row)
        db.flush()
        return {"id": row.id, **payload.model_dump()}


@app.patch("/api/shelters/{shelter_id}")
def update_shelter(shelter_id: int, payload: dict[str, Any], user: User = Depends(allow("ADMIN"))):
    with Session.begin() as db:
        row = db.get(Shelter, shelter_id)
        if not row:
            raise HTTPException(404, "Shelter not found")
        for key, value in payload.items():
            if key in {"name", "place", "latitude", "longitude", "capacity", "occupied", "status"}:
                setattr(row, key, value)
        if row.occupied > row.capacity:
            raise HTTPException(422, "Occupancy cannot exceed capacity")
        return {"id": row.id, "name": row.name, "place": row.place, "capacity": row.capacity, "occupied": row.occupied, "status": row.status}


@app.get("/api/missions")
def list_missions(user: User = Depends(allow("ADMIN", "RESPONDER"))):
    with Session() as db:
        rows = db.scalars(select(Mission).order_by(Mission.assigned_at.desc())).all()
        return [{"id": m.id, "incident_id": m.incident_id, "resource_id": m.resource_id, "unit": db.get(Resource, m.resource_id).name, "status": m.status, "assigned_at": m.assigned_at.isoformat()} for m in rows]


@app.post("/api/missions", status_code=201)
async def assign_mission(payload: AssignmentIn, user: User = Depends(allow("ADMIN"))):
    with Session.begin() as db:
        incident, resource = db.get(Incident, payload.incident_id), db.get(Resource, payload.resource_id)
        if not incident or not resource:
            raise HTTPException(404, "Incident or resource not found")
        if incident.status in {"Resolved", "Cancelled"} or resource.status != "Available":
            raise HTTPException(409, "Incident is closed or resource is not available")
        n = db.scalar(select(Mission.id).order_by(Mission.id.desc()).limit(1))
        num = int(n.split("-")[-1]) + 1 if n else 1
        mission = Mission(id=f"MSN-{num:03d}", incident_id=incident.id, resource_id=resource.id, assigned_by=user.id, status="Assigned")
        db.add(mission)
        incident.status, incident.unit = "Assigned", resource.name
        resource.status = "Assigned"
        db.add(IncidentUpdate(incident_id=incident.id, actor_id=user.id, status="Assigned", note=f"Assigned {resource.name}"))
        result = {"id": mission.id, "incident_id": incident.id, "resource_id": resource.id, "status": mission.status}
    await broadcast({"type": "mission.created", "mission": result})
    return result


@app.patch("/api/missions/{mission_id}/status")
async def update_mission(mission_id: str, payload: StatusIn, user: User = Depends(allow("ADMIN", "RESPONDER"))):
    transitions = {"Assigned": "En route", "En route": "Arrived", "Arrived": "Completed"}
    with Session.begin() as db:
        mission = db.get(Mission, mission_id)
        if not mission:
            raise HTTPException(404, "Mission not found")
        if payload.status != transitions.get(mission.status):
            raise HTTPException(409, f"Mission must advance from {mission.status} to {transitions.get(mission.status, 'closed')}")
        mission.status = payload.status
        incident, resource = db.get(Incident, mission.incident_id), db.get(Resource, mission.resource_id)
        if payload.status == "Completed":
            mission.completed_at = datetime.now(timezone.utc)
            incident.status, resource.status = "Resolved", "Available"
        elif payload.status == "En route":
            incident.status, resource.status = "En route", "En route"
        elif payload.status == "Arrived":
            incident.status = "Arrived"
        db.add(IncidentUpdate(incident_id=incident.id, actor_id=user.id, status=incident.status, note=payload.note or f"Mission {mission.id}: {mission.status}"))
        result = {"id": mission.id, "status": mission.status, "incident_id": incident.id, "resource_id": resource.id}
    await broadcast({"type": "mission.updated", "mission": result})
    return result


@app.get("/api/alerts")
def list_alerts():
    with Session() as db:
        return [{"id": a.id, "title": a.title, "message": a.message, "severity": a.severity, "area": a.area, "created_at": a.created_at.isoformat()} for a in db.scalars(select(Alert).order_by(Alert.created_at.desc())).all()]


@app.post("/api/alerts", status_code=201)
async def create_alert(payload: AlertIn, user: User = Depends(allow("ADMIN"))):
    with Session.begin() as db:
        row = Alert(**payload.model_dump())
        db.add(row)
        db.flush()
        result = {"id": row.id, "title": row.title, "message": row.message, "severity": row.severity, "area": row.area, "created_at": row.created_at.isoformat()}
    await broadcast({"type": "alert.created", "alert": result})
    return result


@app.get("/api/incidents/{incident_id}/history")
def incident_history(incident_id: str, user: User = Depends(allow("ADMIN", "RESPONDER"))):
    with Session() as db:
        return [{"status": u.status, "note": u.note, "actor_id": u.actor_id, "created_at": u.created_at.isoformat()} for u in db.scalars(select(IncidentUpdate).where(IncidentUpdate.incident_id == incident_id).order_by(IncidentUpdate.created_at)).all()]


@app.post("/api/incidents/{incident_id}/image", status_code=201)
async def upload_incident_image(incident_id: str, image: UploadFile = File(...), user: User = Depends(user_dep)):
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, "Upload a JPEG, PNG, or WebP image")
    data = await image.read(5_000_001)
    if len(data) > 5_000_000:
        raise HTTPException(413, "Images must be 5 MB or smaller")
    with Session() as db:
        incident = db.get(Incident, incident_id)
        if not incident or user.role == "CITIZEN" and incident.reporter_id != user.id:
            raise HTTPException(404, "Incident not found")
    folder = APP_DIR.parent / "uploads"
    folder.mkdir(exist_ok=True)
    filename = f"{incident_id}-{secrets.token_hex(8)}.{image.content_type.split('/')[-1].replace('jpeg','jpg')}"
    (folder / filename).write_bytes(data)
    return {"filename": filename, "url": f"/uploads/{filename}"}


@app.post("/api/simulation/run")
async def simulate(user: User = Depends(allow("ADMIN"))):
    cases = [("Flash flooding", "Palasia · YN Road", "Rapid water rise reported near homes; urgent support requested.", 22, True, 22.726, 75.884), ("Road accident", "Ring Road · Rau junction", "Two-vehicle collision; first aid requested.", 4, True, 22.643, 75.812), ("Power outage", "Scheme 78 · Sector B", "Power lines down after strong winds.", 0, False, 22.754, 75.900)]
    case = secrets.choice(cases)
    score, severity = score_incident(case[3], case[4], case[0], case[2])
    with Session.begin() as db:
        n = db.scalar(select(Incident.id).order_by(Incident.id.desc()).limit(1))
        num = int(n.split("-")[-1]) + 1 if n and n.startswith("NAY-") else 1
        row = Incident(id=f"NAY-{num:04d}", type=case[0], place=case[1], description=case[2], people_affected=case[3], medical_required=case[4], latitude=case[5], longitude=case[6], severity_score=score, severity_level=severity, reporter_id=user.id)
        db.add(row)
        db.add(IncidentUpdate(incident_id=row.id, actor_id=user.id, status="Reported", note="Generated by disaster simulation"))
        result = as_incident(row)
    await broadcast({"type": "incident.created", "incident": result, "simulation": True})
    return result


@app.get("/api/analytics")
def analytics(user: User = Depends(allow("ADMIN", "RESPONDER"))):
    with Session() as db:
        rows = db.scalars(select(Incident)).all()
        days = [datetime.now(timezone.utc).date() - timedelta(days=offset) for offset in range(6, -1, -1)]
        return {"total": len(rows), "active": sum(i.status not in {"Resolved", "Cancelled"} for i in rows), "resolved": sum(i.status == "Resolved" for i in rows), "critical": sum(i.severity_level == "Critical" and i.status not in {"Resolved", "Cancelled"} for i in rows), "average_severity": round(sum(i.severity_score for i in rows) / max(1, len(rows))), "by_type": {kind: sum(i.type == kind for i in rows) for kind in sorted({i.type for i in rows})}, "daily": [{"day": day.isoformat(), "count": sum(i.created_at.date() == day for i in rows if i.created_at)} for day in days]}


@app.post("/api/incidents/check-duplicates")
def check_duplicates(payload: IncidentIn, user: User = Depends(user_dep)):
    with Session() as db:
        matches = duplicate_candidates(payload, db.scalars(select(Incident)).all())
    return {"possible_duplicates": matches, "review_required": bool(matches)}


@app.post("/api/intelligence/classify")
def classify_incident(payload: ClassifyIn, user: User = Depends(user_dep)):
    text_value = payload.text.lower()
    categories = {
        "Urban flooding": ("flood", "waterlogging", "water logging", "overflow", "inundat", "submerged"),
        "Building fire": ("fire", "smoke", "burning", "flame", "blaze"),
        "Road accident": ("accident", "collision", "crash", "vehicle", "road traffic"),
        "Medical emergency": ("injury", "injured", "medical", "unconscious", "ambulance", "bleeding"),
        "Infrastructure damage": ("bridge", "road damage", "power line", "building collapse", "structural", "landslide"),
    }
    matches = {label: sum(text_value.count(term) for term in terms) for label, terms in categories.items()}
    best = max(matches, key=matches.get)
    count = matches[best]
    return {"suggested_type": best if count else "Other emergency", "confidence": min(0.94, round(0.38 + count * 0.17, 2)) if count else 0.25, "matched_signals": matches[best] if count else 0, "model": "transparent keyword baseline", "requires_human_review": True}


@app.get("/api/incidents/{incident_id}/recommendations")
def resource_recommendations(incident_id: str, user: User = Depends(allow("ADMIN", "RESPONDER"))):
    with Session() as db:
        incident = db.get(Incident, incident_id)
        if not incident:
            raise HTTPException(404, "Incident not found")
        return {"incident_id": incident_id, "recommendations": recommend_resources(incident, db.scalars(select(Resource)).all())}


@app.get("/api/routes/{incident_id}")
def route_to_incident(incident_id: str, resource_id: int, user: User = Depends(allow("ADMIN", "RESPONDER"))):
    with Session() as db:
        incident, resource = db.get(Incident, incident_id), db.get(Resource, resource_id)
        if not incident or not resource:
            raise HTTPException(404, "Incident or resource not found")
        distance = haversine_km(resource.latitude, resource.longitude, incident.latitude, incident.longitude)
        fallback = {"provider": "straight-line estimate", "distance_km": round(distance, 2), "duration_minutes": round(distance * 3 + 5), "coordinates": [[resource.longitude, resource.latitude], [incident.longitude, incident.latitude]]}
        routing_url = os.getenv("NAYAN_ROUTING_URL", "").rstrip("/")
        if not routing_url:
            return {**fallback, "provider": "straight-line estimate · configure a private OSRM service for road routing"}
        try:
            from urllib.request import Request, urlopen
            url = f"{routing_url}/route/v1/driving/{resource.longitude},{resource.latitude};{incident.longitude},{incident.latitude}?overview=full&geometries=geojson"
            request = Request(url, headers={"User-Agent": "Nayan-Disaster-Management/1.0"})
            with urlopen(request, timeout=3) as response:
                result = json.loads(response.read())
            route = result["routes"][0]
            return {"provider": "OSRM demonstration routing", "distance_km": round(route["distance"] / 1000, 2), "duration_minutes": round(route["duration"] / 60), "coordinates": route["geometry"]["coordinates"]}
        except Exception:
            return fallback


@app.websocket("/api/ws")
async def websocket_updates(socket: WebSocket):
    await socket.accept()
    try:
        hello = await socket.receive_text()
        if not hello.startswith("Bearer "):
            raise HTTPException(401, "Invalid session")
        decode_token(hello[7:])
    except HTTPException:
        await socket.close(code=4401)
        return
    except WebSocketDisconnect:
        return
    subscribers.add(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        subscribers.discard(socket)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(OUTPUTS / "index.html")


@app.get("/uploads/{filename}", include_in_schema=False)
def uploaded_file(filename: str, user: User = Depends(user_dep)):
    safe_name = Path(filename).name
    incident_id = "-".join(Path(safe_name).stem.split("-")[:2])
    with Session() as db:
        incident = db.get(Incident, incident_id)
        if not incident or user.role == "CITIZEN" and incident.reporter_id != user.id:
            raise HTTPException(404, "Image not found")
    path = APP_DIR.parent / "uploads" / safe_name
    if not path.is_file():
        raise HTTPException(404, "Image not found")
    return FileResponse(path)


@app.get("/{asset:path}", include_in_schema=False)
def static_asset(asset: str):
    if asset.startswith(("api/", "uploads/")):
        raise HTTPException(404, "Not found")
    if asset not in {"index.html", "app-remote.js", "favicon.ico"}:
        raise HTTPException(404, "Not found")
    path = OUTPUTS / asset
    if not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path)
