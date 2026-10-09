"""GramSetu Stage 2.5 - evidence validation layer.

Validates a Stage 2 underlying-problem cluster using complaint meaning plus
available photo, location, resource and temporal evidence. It does not change
official priority and does not silently merge/split clusters.
"""
import base64
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from datetime import datetime

AI_MODEL_DEFAULT = "gemini-3.5-flash-lite"


def _load_dotenv():
    path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(path):
        return
    try:
        for line in open(path, "r", encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


_load_dotenv()


def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())


def _schema():
    return {
        "type": "OBJECT",
        "properties": {
            "decision": {"type": "STRING", "enum": ["CONFIRMED", "POSSIBLE", "CONFLICT"]},
            "validated_confidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
            "semantic_evidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
            "photo_evidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
            "location_evidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
            "temporal_evidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
            "resource_evidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
            "asset_match": {"type": "STRING", "enum": ["SAME_ASSET", "LIKELY_SAME_ASSET", "UNKNOWN", "DIFFERENT_ASSET"]},
            "reason": {"type": "STRING"},
            "conflicts": {"type": "STRING"},
            "review_required": {"type": "BOOLEAN"},
        },
        "required": [
            "decision", "validated_confidence", "semantic_evidence", "photo_evidence",
            "location_evidence", "temporal_evidence", "resource_evidence", "asset_match",
            "reason", "conflicts", "review_required"
        ]
    }


def _extract_json(payload):
    for candidate in payload.get("candidates", []) if isinstance(payload, dict) else []:
        content = candidate.get("content", {}) if isinstance(candidate, dict) else {}
        for part in content.get("parts", []) if isinstance(content, dict) else []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return part["text"]
    return ""


def _image_part(value):
    """Return Gemini inline_data for data-URI images; ignore remote URLs safely."""
    value = str(value or "")
    if not value.startswith("data:image/") or "," not in value:
        return None
    header, encoded = value.split(",", 1)
    mime = header.split(";", 1)[0].replace("data:", "").strip() or "image/jpeg"
    try:
        raw = base64.b64decode(encoded, validate=False)
    except Exception:
        return None
    # Keep requests bounded for the local prototype.
    if not raw or len(raw) > 8 * 1024 * 1024:
        return None
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(raw).decode("ascii")}}


def _gemini_validate(cluster, members):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None, "No Gemini API key configured."

    model = os.environ.get("GEMINI_MODEL", AI_MODEL_DEFAULT).strip() or AI_MODEL_DEFAULT
    if model == "gemini-2.5-flash-lite":
        model = AI_MODEL_DEFAULT

    # Keep the text payload compact. Images are added separately when they are real data URIs.
    reports = []
    image_parts = []
    for i, m in enumerate(members[:8], 1):
        reports.append({
            "report_number": i,
            "complaint_id": m.get("complaint_id", ""),
            "description": m.get("description", ""),
            "category": m.get("category", ""),
            "village": m.get("complaint_village", ""),
            "district": m.get("complaint_district", ""),
            "submitted_at": m.get("submitted_at", ""),
            "photo_available": bool(m.get("photo_before")),
            "photo_caption": m.get("before_caption", ""),
            "gps_location": m.get("gps_location", ""),
            "affected_resource": m.get("affected_resource", ""),
            "problem_type": m.get("problem_type", ""),
        })
        part = _image_part(m.get("photo_before"))
        if part:
            image_parts.append(part)
            reports[-1]["image_included"] = True
        else:
            reports[-1]["image_included"] = False

    prompt = f"""
You are Stage 2.5 of Gram Setu, an evidence validation layer after Stage 2 clustering.
Stage 2 already grouped these complaints as one likely underlying real-world problem.
Your job is NOT to make an official government decision and NOT to set priority.
Your job is to validate or challenge the grouping using independent evidence.

Cluster:
{json.dumps({k: cluster.get(k, '') for k in ['cluster_id','title','category','problem_type','affected_resource','scope','district','village','confidence_score','member_count']}, ensure_ascii=False, indent=2)}

Reports:
{json.dumps(reports, ensure_ascii=False, indent=2)}

Validation rules:
- Compare underlying meaning, not just keywords.
- Location is strong evidence. Same village/locality supports a shared incident/asset.
- Time proximity supports a shared ongoing incident, but different dates do not automatically disprove it.
- Affected resource and problem type should be consistent unless different symptoms clearly point to one shared asset/system.
- If photos are available, inspect them. Different angles of the SAME transformer, pole, pipeline, road damage, handpump, etc. are strong evidence of the same physical asset. Do not treat different angles as different problems.
- If photos visibly show DIFFERENT physical assets, treat that as conflict even if the text is similar.
- Never claim a photo proves identity when the visual evidence is unclear; use UNKNOWN/LIKELY_SAME_ASSET.
- A remote photo URL may be present but is not available to you as an image in this request; rely on its existence/caption only.
- validated_confidence is a fresh confidence after evidence validation. It must NOT be calculated by simply adding Stage 2 confidence to other scores.
- CONFIRMED is appropriate when independent evidence strongly supports the same real-world problem.
- POSSIBLE is appropriate when evidence is incomplete or mixed but there is no strong contradiction.
- CONFLICT is appropriate when evidence indicates the reports refer to different real-world problems/assets.
- review_required should be true for POSSIBLE or CONFLICT, or whenever evidence is materially incomplete.
Return only the requested JSON.
""".strip()

    parts = [{"text": prompt}]
    parts.extend(image_parts)
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _schema(),
            "temperature": 0.05,
        },
    }
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
        parsed = json.loads(_extract_json(payload))
        if not isinstance(parsed, dict):
            raise ValueError("Gemini returned a non-object result")
        return parsed, ""
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return None, f"Gemini HTTP {exc.code}: {detail[:500]}"
    except Exception as exc:
        return None, f"Gemini validation error: {exc}"


def _fallback_validate(cluster, members):
    """Conservative local evidence validator when Gemini is unavailable."""
    if len(members) < 2:
        return {
            "decision": "POSSIBLE", "validated_confidence": float(cluster.get("confidence_score") or 0),
            "semantic_evidence": float(cluster.get("confidence_score") or 0),
            "photo_evidence": 0.0, "location_evidence": 1.0 if cluster.get("village") else 0.0,
            "temporal_evidence": 0.0, "resource_evidence": 0.0, "asset_match": "UNKNOWN",
            "reason": "Only one report is available, so independent evidence cannot validate the cluster yet.",
            "conflicts": "", "review_required": True,
        }
    villages = {_norm(m.get("complaint_village")) for m in members if _norm(m.get("complaint_village"))}
    categories = {_norm(m.get("category")) for m in members if _norm(m.get("category"))}
    same_village = len(villages) <= 1 and bool(villages)
    same_category = len(categories) <= 1 and bool(categories)
    base = float(cluster.get("confidence_score") or 0)
    confidence = min(0.92, max(base, 0.55 + (0.22 if same_village else 0) + (0.12 if same_category else 0)))
    return {
        "decision": "CONFIRMED" if same_village and same_category and confidence >= 0.75 else "POSSIBLE",
        "validated_confidence": round(confidence, 3),
        "semantic_evidence": round(base, 3), "photo_evidence": 0.0,
        "location_evidence": 1.0 if same_village else 0.45,
        "temporal_evidence": 0.0, "resource_evidence": 0.5 if same_category else 0.2,
        "asset_match": "UNKNOWN",
        "reason": "Local fallback supports the grouping from shared location/category signals; photo identity was not independently verified.",
        "conflicts": "",
        "review_required": True,
    }


def validate_cluster(cluster, members, db):
    result, error = _gemini_validate(cluster, members)
    provider = "gemini" if result else "rule-based-fallback"
    model = os.environ.get("GEMINI_MODEL", AI_MODEL_DEFAULT).strip() or AI_MODEL_DEFAULT
    if model == "gemini-2.5-flash-lite":
        model = AI_MODEL_DEFAULT
    if not result:
        result = _fallback_validate(cluster, members)

    result["validated_confidence"] = max(0.0, min(1.0, float(result.get("validated_confidence") or 0)))
    result["stage2_confidence"] = float(cluster.get("confidence_score") or 0)
    result["ai_provider"] = provider
    result["ai_model"] = model
    result["ai_error"] = error
    result["validated_at"] = datetime.now().isoformat()
    result["member_count"] = len(members)
    db.save_cluster_validation(cluster["cluster_id"], result)
    return db.get_cluster_validation(cluster["cluster_id"])
