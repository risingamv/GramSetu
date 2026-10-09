# GramSetu Stage 2.5 — Evidence Validation

Stage 2.5 sits between **AI clustering (Stage 2)** and future **AI priority/decision support (Stage 3)**.

## Purpose

Stage 2 answers:
> "Do these complaints look like the same underlying problem?"

Stage 2.5 asks:
> "What independent evidence supports or challenges that grouping?"

It validates the cluster using:

- complaint meaning / semantic consistency
- photo evidence when available
- physical asset consistency (for example, the same transformer photographed from different angles)
- village / location consistency
- time proximity / ongoing incident evidence
- affected resource consistency
- cross-report conflicts

## Confidence handling

Stage 2 confidence is preserved as the original clustering signal.

Stage 2.5 produces a **new validated confidence**. It is **not** calculated by simply adding percentages.

Example:

- Stage 2 = 85%
- photos + same village + same transformer + compatible timing
- Stage 2.5 validated confidence = 94%

If evidence conflicts:

- Stage 2 = 88%
- photos show different transformers
- Stage 2.5 may return 41% and `CONFLICT`
- the cluster should be reviewed rather than treated as confirmed

## Decisions

- `CONFIRMED` — independent evidence strongly supports the same real-world problem.
- `POSSIBLE` — evidence is incomplete or mixed; official review is recommended.
- `CONFLICT` — evidence suggests different real-world problems/assets; review is required.

Stage 2.5 **does not change official priority** and does not make the government's final decision.

## Photo handling

Citizen-uploaded image data URIs can be sent to Gemini as inline image evidence. Remote image URLs are recorded as available evidence but are not fetched automatically by the validator.

The validator intentionally keeps the normal admin cluster member response lightweight; photo payloads are only loaded by the validation path.

## Automatic + manual validation

When a new complaint creates or joins a cluster with at least two reports, GramSetu automatically attempts Stage 2.5 validation.

Admins can also manually re-run validation from:

**Admin → Similar Reports → Validate evidence**

The latest validation is shown separately from Stage 2 confidence.

## Storage

Validation results are stored in `cluster_validations` with:

- Stage 2 confidence
- validated confidence
- decision
- semantic/photo/location/time/resource evidence scores
- asset match assessment
- reason and conflicts
- provider/model/error
- validation timestamp

This history allows future Stage 3 decision-support logic to use validated evidence without destroying the original Stage 2 signal.
