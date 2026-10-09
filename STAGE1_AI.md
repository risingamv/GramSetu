# GramSetu Stage 1 — Gemini AI Complaint Analysis

This build adds optional Google Gemini analysis to newly submitted complaints.

## What happens

1. Citizen submits a complaint.
2. GramSetu sends the complaint text plus village/district context from the backend to Gemini.
3. Gemini returns strict JSON containing category, problem type, affected resource, scope, duration, estimated affected people, severity, and confidence.
4. GramSetu stores that analysis separately in `ai_analyses`.
5. Existing deterministic department/SLA routing remains unchanged.
6. If Gemini is not configured or the request fails, GramSetu falls back to the local rule-based classifier.

## Gemini configuration

```bash
export GEMINI_API_KEY="your_gemini_api_key_here"
export GEMINI_MODEL="gemini-3.5-flash-lite"
python3 app.py
```

The API key must stay on the server and must never be placed in browser JavaScript.

## Scope

Stage 1 is complaint understanding only. It does not yet perform embeddings, semantic similarity, clustering, or mass-impact priority scoring.

AI output is advisory; government officials retain final decision authority.
