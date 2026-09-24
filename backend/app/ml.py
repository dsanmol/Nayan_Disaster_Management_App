"""Small, local text models for Nayan decision support.

Training examples are curated prototype examples, not an operationally validated dataset.
Keep predictions advisory and require a human to confirm incident type and duplicates.
"""
from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.pipeline import make_pipeline

_TRAINING = {
    "Urban flooding": [
        "flood water is entering houses after heavy rain", "streets are submerged and people are stranded",
        "waterlogging blocks the road and homes", "river overflow threatens nearby neighborhoods",
        "rainwater has risen rapidly inside ground floor homes", "people need rescue from flood water",
        "drain overflow inundated the market", "flash flood swept through the village",
        "cars are stuck in deep water on the street", "water is rising near the bridge after rainfall",
        "basement is flooded and residents need help", "heavy rain caused severe waterlogging",
    ],
    "Building fire": [
        "smoke and flames coming from a commercial building", "house is burning and occupants are evacuating",
        "fire reported on the second floor", "warehouse blaze spreading to nearby structures",
        "electrical fire with thick smoke in an apartment", "shop caught fire and people are trapped inside",
        "flames visible from a factory roof", "kitchen fire spreading through the home",
        "building fire requires fire brigade response", "smoke reported from an office tower",
        "gas explosion started a fire in the market", "wildfire is approaching homes",
    ],
    "Road accident": [
        "multiple vehicles collided at the intersection", "car crash with injured passengers on the highway",
        "bus overturned and is blocking traffic", "motorcycle accident needs ambulance assistance",
        "truck struck a vehicle and people are hurt", "road traffic collision near the junction",
        "two cars crashed on the northbound lane", "vehicle hit a pedestrian on the road",
        "serious accident with bleeding passengers", "van rolled over beside the bridge",
        "traffic crash has trapped a driver", "collision involving a bus and a truck",
    ],
    "Medical emergency": [
        "person is unconscious and needs urgent medical attention", "patient having difficulty breathing",
        "elderly resident collapsed and needs an ambulance", "severe bleeding after a fall at home",
        "child is injured and requires medical help", "person having a heart attack",
        "multiple people are sick and need urgent treatment", "patient is not responding",
        "requesting ambulance for a medical emergency", "resident suffered a seizure",
        "person has a serious burn injury", "urgent first aid needed for an injured person",
    ],
    "Infrastructure damage": [
        "bridge has collapsed and road is impassable", "power lines have fallen across the street",
        "landslide damaged homes and blocked the road", "large crack in the building after earthquake",
        "road has washed away and vehicles cannot pass", "damaged water main flooding the street",
        "building structure partially collapsed", "storm brought down an electric pole",
        "retaining wall collapsed near houses", "gas pipeline damaged during construction",
        "road surface has a deep sinkhole", "structural damage makes the school unsafe",
    ],
    "Other emergency": [
        "people are stranded after a severe storm", "missing residents need help locating them",
        "chemical spill reported near the factory", "strong winds damaged several homes",
        "emergency assistance requested in the neighborhood", "hazardous material leak near the station",
        "earthquake shaking reported and residents need support", "evacuation requested due to a nearby threat",
        "tree fallen across access road after a storm", "water supply stopped across several blocks",
        "people need shelter after severe weather", "emergency services requested for a safety concern",
    ],
}

_texts = [example for label, examples in _TRAINING.items() for example in examples]
_labels = [label for label, examples in _TRAINING.items() for _ in examples]
_classifier = make_pipeline(
    TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True),
    LogisticRegression(max_iter=1000, class_weight="balanced", random_state=17),
)
_classifier.fit(_texts, _labels)


def classify_incident(text: str) -> dict[str, object]:
    probabilities = _classifier.predict_proba([text])[0]
    classes = _classifier.classes_
    best = int(probabilities.argmax())
    confidence = float(probabilities[best])
    return {
        "suggested_type": str(classes[best]),
        "confidence": round(confidence, 3),
        "matched_signals": 1,
        "model": "local TF-IDF + Logistic Regression (prototype training examples)",
        "requires_human_review": True,
        "low_confidence": confidence < 0.42,
    }


def text_similarity(left: str, right: str) -> float:
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True)
    try:
        matrix = vectorizer.fit_transform([left, right])
    except ValueError:  # Empty vocabulary when a report contains only stopwords.
        return 0.0
    return float(cosine_similarity(matrix[0:1], matrix[1:2])[0, 0])
