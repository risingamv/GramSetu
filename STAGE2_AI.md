# GramSetu Stage 2 — Underlying Problem Clustering

Stage 2 groups different citizen complaints that are likely describing the **same real-world problem**.

## What it does

Example:

- “The handpump near the school is broken.”
- “Children have no drinking water near the school because the tap isn't working.”
- “Water supply at the primary school has stopped.”

These may become one underlying problem cluster instead of three unrelated tickets.

## Signals used

Stage 2 considers:

- complaint meaning / semantic similarity
- Stage 1 category
- Stage 1 problem type
- affected resource
- scope
- village and district
- whether the evidence is strong enough to merge

Different resources can still belong to one cluster when they are clearly symptoms of the same underlying problem. Similar wording alone is not enough.

## Gemini decision

When `GEMINI_API_KEY` is configured, the backend sends a small set of candidate clusters to Gemini. Gemini returns:

- `MATCH` or `NEW`
- selected cluster ID
- clustering confidence from 0–1
- a short reason

Automatic matching requires at least **75% Gemini confidence**. Ambiguous cases create a new cluster rather than forcing a merge.

If Gemini is unavailable, GramSetu uses a conservative local similarity fallback. The fallback never blocks complaint submission.

## Storage

Stage 2 adds:

- `problem_clusters` — one row per underlying problem
- `problem_cluster_members` — links complaints to their cluster

The original complaint and Stage 1 AI analysis remain unchanged.

## Admin dashboard

Admins get a **Problem Clusters** tab showing:

- cluster ID
- underlying problem title
- village / district
- category and affected resource
- number of complaints
- clustering confidence
- member complaints and the reason they were grouped

A **Cluster** button on each complaint can rerun Stage 2 for that complaint.

## Stage 2 does NOT

- change official priority
- decide LOW / MEDIUM / HIGH
- close or resolve complaints
- make a government decision

Those belong to Stage 3 and official review.
