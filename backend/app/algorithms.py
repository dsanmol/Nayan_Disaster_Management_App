from __future__ import annotations

import math
import re
from difflib import SequenceMatcher
from typing import Iterable


def haversine_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    earth_km = 6371.0088
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat - a_lat), math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * earth_km * math.asin(min(1, math.sqrt(h)))


def words(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def duplicate_candidates(report, incidents: Iterable, radius_km: float = 1.0):
    candidates = []
    report_text = words(f"{report.type} {report.description}")
    for incident in incidents:
        if incident.status in {"Resolved", "Cancelled"}:
            continue
        distance = haversine_km(report.latitude, report.longitude, incident.latitude, incident.longitude)
        similarity = SequenceMatcher(None, report_text, words(f"{incident.type} {incident.description}")).ratio()
        same_type = words(report.type) == words(incident.type)
        if distance <= radius_km and (similarity >= 0.28 or same_type):
            candidates.append({"incident_id": incident.id, "type": incident.type, "place": incident.place, "distance_km": round(distance, 2), "text_similarity": round(similarity, 2), "severity": incident.severity_level})
    return sorted(candidates, key=lambda row: (row["distance_km"], -row["text_similarity"]))[:5]


def recommend_resources(incident, resources: Iterable):
    required = {"flood": {"rescue team", "ambulance"}, "fire": {"fire team", "ambulance"}, "accident": {"ambulance"}, "medical": {"ambulance"}, "collapse": {"rescue team", "ambulance"}}
    kind = incident.type.lower()
    wanted = next((skills for keyword, skills in required.items() if keyword in kind), set())
    recs = []
    for resource in resources:
        if resource.status != "Available":
            continue
        distance = haversine_km(incident.latitude, incident.longitude, resource.latitude, resource.longitude)
        capability = 1.0 if resource.type.lower() in wanted else 0.35
        skill_match = 0.25 if any(word in (resource.skills or "").lower() for word in kind.split()) else 0
        score = round(max(0, 100 - distance * 4) * capability + skill_match * 10, 1)
        recs.append({"resource_id": resource.id, "name": resource.name, "type": resource.type, "distance_km": round(distance, 2), "recommendation_score": score, "reason": "Capability match · closest available unit" if capability > 0.5 else "Available unit · secondary capability"})
    return sorted(recs, key=lambda row: (-row["recommendation_score"], row["distance_km"]))[:5]
