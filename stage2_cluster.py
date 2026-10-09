"""GramSetu Stage 2 - underlying problem clustering.

Stage 2 groups different citizen complaints that likely describe the same
real-world problem. It is advisory only and never changes official priority.
"""
import json
import os
import re
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from datetime import datetime

AI_MODEL_DEFAULT = "gemini-3.5-flash-lite"


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _tokens(value):
    return set(re.findall(r"[\w\u0900-\u097F]+", _norm(value)))


def _local_similarity(a, b):
    """Conservative dependency-free fallback similarity from text + structured fields."""
    text_a = _norm(a.get("description"))
    text_b = _norm(b.get("description"))
    ta, tb = _tokens(text_a), _tokens(text_b)
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    sequence = SequenceMatcher(None, text_a, text_b).ratio()

    structured = 0.0
    checks = 0
    for key, weight in [("category", .18), ("district", .08), ("village", .20),
                        ("problem_type", .22), ("affected_resource", .10), ("scope", .10)]:
        va, vb = _norm(a.get(key)), _norm(b.get(key))
        if va and vb and va != "unknown":
            checks += weight
            if va == vb:
                structured += weight
    base = (jaccard * .22) + (sequence * .18)
    if checks:
        base += (structured / checks) * .60
    return round(min(1.0, base), 3)


def _extract_json(payload):
    for candidate in payload.get("candidates", []) if isinstance(payload, dict) else []:
        content = candidate.get("content", {}) if isinstance(candidate, dict) else {}
        for part in content.get("parts", []) if isinstance(content, dict) else []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return part["text"]
    return ""


def _schema():
    return {
        "type": "OBJECT",
        "properties": {
            "decision": {"type": "STRING", "enum": ["MATCH", "NEW"]},
            "cluster_id": {"type": "STRING"},
            "confidence_score": {"type": "NUMBER", "minimum": 0, "maximum": 1},
            "reason": {"type": "STRING"}
        },
        "required": ["decision", "cluster_id", "confidence_score", "reason"]
    }


def _gemini_choose(complaint, candidates):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key or not candidates:
        return None, "No Gemini key or no candidate clusters."

    model = os.environ.get("GEMINI_MODEL", AI_MODEL_DEFAULT).strip() or AI_MODEL_DEFAULT
    if model == "gemini-2.5-flash-lite":
        model = AI_MODEL_DEFAULT

    compact = []
    for c in candidates:
        compact.append({
            "cluster_id": c["cluster_id"],
            "title": c.get("title", ""),
            "category": c.get("category", ""),
            "problem_type": c.get("problem_type", ""),
            "affected_resource": c.get("affected_resource", ""),
            "scope": c.get("scope", ""),
            "district": c.get("district", ""),
            "village": c.get("village", ""),
            "member_count": c.get("member_count", 0),
        })

    prompt = f"""
You are Stage 2 of Gram Setu. Decide whether this new citizen complaint belongs to
ONE of the existing underlying-problem clusters, or whether it starts a NEW cluster.

New complaint:
{json.dumps(complaint, ensure_ascii=False, indent=2)}

Existing candidate clusters:
{json.dumps(compact, ensure_ascii=False, indent=2)}

Rules:
- Match the underlying real-world problem, not just shared words.
- Category should normally agree.
- Location matters strongly: same village/locality is strong evidence; different
  villages can still match only when the complaint clearly describes one shared
  infrastructure/service problem.
- Different affected resources can still belong together when they are clearly
  different symptoms of the same underlying problem.
- Do NOT merge merely because both complaints mention the same category or resource.
- If evidence is weak or ambiguous, choose NEW.
- cluster_id MUST be exactly one candidate id when decision=MATCH; use NEW when decision=NEW.
- confidence_score is confidence in the clustering decision, not severity or urgency.
- Return only the requested JSON.
""".strip()

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
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
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw = _extract_json(payload)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Gemini returned a non-object result")
        return parsed, ""
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return None, f"Gemini HTTP {exc.code}: {detail[:500]}"
    except Exception as exc:
        return None, f"Gemini clustering error: {exc}"


def build_complaint_context(grievance, ai):
    return {
        "complaint_id": grievance.get("id", ""),
        "description": grievance.get("description", ""),
        "category": ai.get("category") or grievance.get("category", ""),
        "problem_type": ai.get("problem_type", "Unknown"),
        "affected_resource": ai.get("affected_resource", "Unknown"),
        "scope": ai.get("scope", "Unknown"),
        "duration": ai.get("duration", "Unknown"),
        "district": grievance.get("district", ""),
        "village": grievance.get("village", ""),
    }


def assign_complaint_to_cluster(grievance, ai, db):
    """Assign a complaint to an existing cluster or create a new one."""
    context = build_complaint_context(grievance, ai)
    candidates = db.get_cluster_candidates(context, limit=12)

    # Use local scoring to rank candidates and avoid sending unrelated clusters to Gemini.
    ranked = []
    for c in candidates:
        score = _local_similarity(context, {
            "description": c.get("representative_description", ""),
            "category": c.get("category", ""),
            "district": c.get("district", ""),
            "village": c.get("village", ""),
            "problem_type": c.get("problem_type", ""),
            "affected_resource": c.get("affected_resource", ""),
            "scope": c.get("scope", ""),
        })
        c["local_score"] = score
        ranked.append(c)
    ranked.sort(key=lambda x: x["local_score"], reverse=True)
    ranked = ranked[:6]

    decision = None
    error = ""
    if ranked:
        decision, error = _gemini_choose(context, ranked)

    provider = "gemini" if decision else "rule-based-fallback"
    status = "success" if decision else "fallback"
    model = os.environ.get("GEMINI_MODEL", AI_MODEL_DEFAULT).strip() or AI_MODEL_DEFAULT
    if model == "gemini-2.5-flash-lite":
        model = AI_MODEL_DEFAULT

    selected = None
    confidence = 0.0
    reason = "No existing cluster was a sufficiently strong match."
    if decision:
        if decision.get("decision") == "MATCH" and decision.get("cluster_id") in {c["cluster_id"] for c in ranked}:
            confidence = float(decision.get("confidence_score") or 0)
            # Require a conservative threshold for automatic grouping.
            if confidence >= 0.75:
                selected = next(c for c in ranked if c["cluster_id"] == decision["cluster_id"])
                reason = str(decision.get("reason") or "Matched underlying problem.")
            else:
                reason = "Gemini found a possible match, but confidence was below the automatic-match threshold."
        else:
            confidence = float(decision.get("confidence_score") or 0)
            reason = str(decision.get("reason") or reason)
    else:
        # Conservative local fallback: only auto-match very strong signals.
        if ranked and ranked[0]["local_score"] >= 0.78:
            selected = ranked[0]
            confidence = ranked[0]["local_score"]
            reason = "Local fallback found a strong match using complaint meaning and structured location/category signals."
        elif ranked:
            confidence = ranked[0]["local_score"]
            reason = "No sufficiently strong local match; created a new cluster."

    if selected:
        db.add_cluster_member(selected["cluster_id"], grievance["id"], confidence, reason, provider, model, error)
        db.touch_cluster(selected["cluster_id"], confidence)
        return db.get_cluster(selected["cluster_id"])

    title = _make_cluster_title(context)
    cluster_id = db.create_cluster(
        title=title,
        category=context["category"],
        problem_type=context["problem_type"],
        affected_resource=context["affected_resource"],
        scope=context["scope"],
        district=context["district"],
        village=context["village"],
        representative_description=context["description"],
        confidence=confidence,
        provider=provider,
        model=model,
        error=error,
        reason=reason,
    )
    db.add_cluster_member(cluster_id, grievance["id"], confidence, reason, provider, model, error)
    return db.get_cluster(cluster_id)


def _make_cluster_title(context):
    problem = str(context.get("problem_type") or "Underlying grievance").strip()
    resource = str(context.get("affected_resource") or "").strip()
    village = str(context.get("village") or "").strip()
    if problem and problem.lower() != "unknown":
        title = problem
    elif resource and resource.lower() != "unknown":
        title = f"Issue affecting {resource}"
    else:
        title = "Underlying community problem"
    if village:
        title += f" — {village}"
    return title[:180]
