"""
GramSetu (ग्राम सेतु) - AI Triage & Semantic Classifier
Parses rural complaint narratives, extracts domain category, maps to responsible department,
calculates enforceable SLA deadlines, and generates unique Complaint IDs.
"""

import random
import os
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

CATEGORY_RULES = {
    "Electricity": {
        "keywords": ["बिजली", "ट्रांसफार्मर", "करंट", "तार", "electric", "power", "transformer", "voltage", "blackout", "pole", "fuse", "substation"],
        "dept": "State Electricity Distribution Company (DISCOM)",
        "officer": "Er. R. Sharma (Junior Engineer, Local Substation)",
        "sla_hours": 24,
        "default_priority": "High"
    },
    "Water": {
        "keywords": ["पानी", "नल", "हैंडपंप", "जल", "पाइप", "water", "pipeline", "well", "pump", "handpump", "drinking water", "leakage"],
        "dept": "Rural Water Supply & Sanitation (Jal Shakti / PHED)",
        "officer": "S. K. Kadam (Sub-Divisional Officer, PHED)",
        "sla_hours": 48,
        "default_priority": "High"
    },
    "Roads": {
        "keywords": ["सड़क", "गड्ढे", "रास्ता", "पुलिया", "road", "pothole", "bridge", "culvert", "tar", "asphalt", "connectivity", "highway"],
        "dept": "Public Works Department (PWD / Gram Panchayat)",
        "officer": "A. K. Verma (Assistant Engineer, Road Div)",
        "sla_hours": 168,
        "default_priority": "Medium"
    },
    "Sanitation": {
        "keywords": ["कचरा", "नाली", "सफाई", "गंदगी", "drain", "waste", "sanitation", "garbage", "sewer", "septic", "mosquitoes"],
        "dept": "Local Gram Panchayat / Swachh Bharat Mission Cell",
        "officer": "Block Development Officer (Sanitation Cell)",
        "sla_hours": 72,
        "default_priority": "Medium"
    },
    "Agriculture": {
        "keywords": ["फसल", "नहर", "सिंचाई", "बीज", "canal", "crop", "irrigation", "agriculture", "farmer", "fertilizer", "sluice"],
        "dept": "Minor Irrigation & Agriculture Dept (Krishi Vibhag)",
        "officer": "District Agriculture Officer / SDO Irrigation",
        "sla_hours": 48,
        "default_priority": "High"
    },
    "Healthcare": {
        "keywords": ["अस्पताल", "दवा", "डॉक्टर", "phc", "health", "hospital", "nurse", "clinic", "ambulance", "medicine", "doctor"],
        "dept": "Chief Medical Officer (CMO) & District Health Society",
        "officer": "Medical Officer In-Charge (Block PHC)",
        "sla_hours": 24,
        "default_priority": "High"
    },
    "Government scheme": {
        "keywords": ["राशन", "पेंशन", "पीएम किसान", "आवास", "योजना", "ration", "pension", "dbt", "pmay", "scheme", "subsidy", "mgnrega"],
        "dept": "District Collectorate & Social Welfare Department",
        "officer": "Sub-Divisional Magistrate (Grievance Cell)",
        "sla_hours": 120,
        "default_priority": "Medium"
    }
}


def _rule_based_analysis(text):
    """Fast local fallback used when the AI API is not configured or unavailable."""
    text = str(text or "")
    text_lower = text.lower()
    matched_cat = "Other"
    max_matches = 0

    for cat, data in CATEGORY_RULES.items():
        matches = sum(1 for kw in data["keywords"] if kw in text_lower)
        if matches > max_matches:
            max_matches = matches
            matched_cat = cat

    if matched_cat in CATEGORY_RULES:
        rule = CATEGORY_RULES[matched_cat]
        sla_hours = rule["sla_hours"]
        dept = rule["dept"]
        officer = rule["officer"]
        priority = rule["default_priority"]
    else:
        sla_hours = 120
        dept = "Gram Panchayat / Block Development Office"
        officer = "Panchayat Secretary"
        priority = "Standard"

    now = datetime.now()
    deadline = (now + timedelta(hours=sla_hours)).isoformat()

    return {
        "category": matched_cat,
        "problem_type": "Not analyzed by AI",
        "affected_resource": "Unknown",
        "scope": "Unknown",
        "duration": "Unknown",
        "estimated_affected_people": None,
        "severity": "Unknown",
        "department": dept,
        "assigned_officer": officer,
        "priority": priority,
        "sla_hours": sla_hours,
        "sla_deadline": deadline,
        "confidence_score": round(0.94 if max_matches > 0 else 0.70, 2),
        "ai_provider": "rule-based-fallback",
        "ai_status": "fallback"
    }


AI_CATEGORIES = [
    "Water", "Roads", "Electricity", "Sanitation", "Healthcare",
    "Education", "Agriculture", "Public Distribution", "Government Services",
    "Street Lighting", "Drainage", "Other"
]

AI_SCHEMA = {
    # This schema is intentionally limited to the fields supported by Gemini's
    # generateContent responseSchema proto. Do not add JSON-Schema-only keys
    # such as additionalProperties, and do not use a type array for nullable
    # values here.
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": AI_CATEGORIES},
        "problem_type": {"type": "string"},
        "affected_resource": {"type": "string"},
        "scope": {"type": "string"},
        "duration": {"type": "string"},
        # Gemini responseSchema uses the protobuf Schema type. Use 0 as the
        # explicit sentinel for "not stated" and normalize it to None below.
        "estimated_affected_people": {"type": "integer", "minimum": 0},
        "severity": {"type": "string", "enum": ["Low", "Medium", "High", "Unknown"]},
        "confidence_score": {"type": "number", "minimum": 0, "maximum": 1}
    },
    "required": [
        "category", "problem_type", "affected_resource", "scope",
        "duration", "estimated_affected_people", "severity", "confidence_score"
    ]
}


def _extract_gemini_response_text(payload):
    """Extract text from a Gemini generateContent response."""
    if not isinstance(payload, dict):
        return ""

    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {}) if isinstance(candidate, dict) else {}
        for part in content.get("parts", []) if isinstance(content, dict) else []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return part["text"]
    return ""


def _gemini_schema(schema):
    """Return the JSON Schema subset accepted by Gemini structured output."""
    if not isinstance(schema, dict):
        return schema

    # Gemini's current REST structured-output format accepts a JSON Schema subset
    # with lowercase JSON Schema type names. Keep the schema simple and recursive.
    result = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            result[key] = {name: _gemini_schema(prop) for name, prop in value.items()}
        elif key == "items" and isinstance(value, dict):
            result[key] = _gemini_schema(value)
        elif key == "type":
            # REST Schema enum values are represented as uppercase names
            # (OBJECT, STRING, ARRAY, INTEGER, NUMBER, NULL, ...).
            if isinstance(value, str):
                result[key] = value.upper()
            elif isinstance(value, list):
                result[key] = [item.upper() if isinstance(item, str) else item for item in value]
            else:
                result[key] = value
        else:
            result[key] = value
    return result


def _gemini_request(endpoint, body, api_key, timeout=60, retries=2):
    """Call Gemini with short retry/backoff for transient network/API delays."""
    import time
    last_error = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Retry only transient rate-limit/server errors. Auth/model/request errors
            # should surface immediately so the UI can explain the real problem.
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                raise
            try:
                exc.read()
            except Exception:
                pass
            last_error = exc
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt >= retries:
                raise
            last_error = exc
        time.sleep(1.5 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("Gemini request failed without a response.")


def analyze_complaint_with_ai(text, village="", district="", category_hint=""):
    """
    Analyze a grievance with Google's Gemini API.

    The API key stays server-side in GEMINI_API_KEY.
    If no key is configured or the API fails, return a safe local fallback.
    """
    fallback = _rule_based_analysis(text)
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return fallback

    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"
    # Google now recommends Gemini 3.5 Flash-Lite for new API users.
    # Automatically migrate the old 2.5 Flash-Lite setting so an existing
    # .env does not silently keep requesting an unavailable model.
    if model == "gemini-2.5-flash-lite":
        model = "gemini-3.5-flash-lite"

    prompt = f"""
Analyze this rural government grievance for Gram Setu.

Citizen complaint:
{text}

Location context:
Village: {village or "Unknown"}
District: {district or "Unknown"}
Citizen-selected category, if any: {category_hint or "None"}

Rules:
- Extract only facts explicitly stated or strongly expressed in the complaint.
- Never invent an affected-person count. Return 0 when it is not stated; Gram Setu will store that as unknown.
- Do not identify or infer the citizen.
- Return only the requested structured fields.
- "problem_type" should describe the concrete underlying issue, not merely repeat the category.
- "affected_resource" should identify the physical/service resource when stated.
- "scope" should be Household, Street, Village, Community, Block, District, or Unknown.
- "duration" should preserve the stated duration; use "Unknown" if absent.
- Severity is Low, Medium, High, or Unknown.
- Confidence is your confidence in this extraction, from 0 to 1.
""".strip()

    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "You are the Gram Setu grievance analysis engine. "
                            "Be conservative and factual. Do not invent missing information.\n\n"
                            + prompt
                        )
                    }
                ]
            }
        ],
        "generationConfig": {
            # Gemini generateContent REST API expects these fields directly
            # inside generationConfig. The responseFormat.text shape belongs
            # to the newer response-format configuration and caused the 400
            # error seen in the browser.
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(AI_SCHEMA),
            "temperature": 0.1,
        },
    }

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + model
        + ":generateContent"
    )
    try:
        payload = _gemini_request(endpoint, body, api_key, timeout=60, retries=2)

        raw = _extract_gemini_response_text(payload)
        if not raw:
            raise ValueError("Gemini returned no text content.")

        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Gemini returned a non-object result.")

        category = parsed.get("category")
        if category not in AI_CATEGORIES:
            raise ValueError("Gemini returned an invalid category.")

        # Gemini responseSchema cannot express nullable integer fields here, so
        # 0 is the API sentinel for "unknown". Convert it back to None for the
        # database/UI layer.
        affected_people = parsed.get("estimated_affected_people", 0)
        if affected_people == 0:
            parsed["estimated_affected_people"] = None

        # Merge AI extraction with the existing deterministic routing/SLA rules.
        routing = CATEGORY_RULES.get(category, {})
        sla_hours = routing.get("sla_hours", 120)
        now = datetime.now()
        result = {
            **parsed,
            "department": routing.get(
                "dept", "Gram Panchayat / Block Development Office"
            ),
            "assigned_officer": routing.get("officer", "Panchayat Secretary"),
            "priority": routing.get("default_priority", "Medium"),
            "sla_hours": sla_hours,
            "sla_deadline": (now + timedelta(hours=sla_hours)).isoformat(),
            "confidence_score": max(0.0, min(1.0, float(parsed.get("confidence_score", 0.0)))),
            "ai_provider": "gemini",
            "ai_model": model,
            "ai_status": "success",
        }
        return result

    except urllib.error.HTTPError as exc:
        # Preserve Gemini's response body so the admin UI/logs show the real
        # reason for a 400/401/403/429 instead of only "HTTP Error 400".
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = ""
        detail = error_body[:1500].strip() or str(exc)
        print(f"Gemini complaint analysis failed: {detail}")
        fallback["ai_error"] = f"Gemini HTTP {exc.code}: {detail}"
        return fallback
    except Exception as exc:
        print(f"Gemini complaint analysis failed: {exc}")
        fallback["ai_error"] = str(exc)
        return fallback

def classify_complaint(text, village="", district="", category_hint=""):
    """
    Backwards-compatible entry point used by Gram Setu.
    Stage 1 now prefers Gemini analysis and falls back safely to local rules.
    """
    return analyze_complaint_with_ai(text, village, district, category_hint)

def generate_complaint_id():
    """Generates complaint ID in format: GRV-2026-NNNNNN"""
    year = datetime.now().year
    rand_digits = random.randint(1000, 9999)
    return f"GRV-{year}-00{rand_digits}"
