# GramSetu (ग्राम सेतु) - AI-Powered Rural Grievance Redressal Platform

An automated, citizen-centric public grievance redressal system designed specifically for rural citizens with multi-dialect voice input, automated AI triage, SLA enforcement, and mandatory citizen confirmation loops. **GramSetu** bridges rural citizens directly to departmental authorities.

---

## 🌟 Key Features

1. **Voice-to-Text & Dialect Processing**:
   - Rural citizens can report grievances by speaking in their regional language (Hindi, Bhojpuri, Marathi, etc.) or typing.
2. **AI Classification & Triage Core**:
   - Automatically detects problem domains:
     - ⚡ **Electricity** (DISCOM / Vidyut Vitran Nigam) - 24 Hours SLA
     - 🚰 **Drinking Water** (Jal Shakti / PHED) - 48 Hours SLA
     - 🛣️ **Roads & Potholes** (PWD / Gram Panchayat) - 7 Days SLA
     - 🚮 **Sanitation & Waste** (Swachh Bharat / Gram Panchayat) - 72 Hours SLA
     - 🌾 **Agriculture & Irrigation** (Krishi Vibhag) - 48 Hours SLA
     - 🏥 **Healthcare & PHC** (Health Dept / CMO) - 24 Hours SLA
     - 📋 **Govt Schemes & DBT** (District Collectorate) - 5 Days SLA
3. **Unique Complaint ID Generation**:
   - Generates format `GRV-2026-NNNNNN` with real-time tracking links.
4. **Official Redressal Dashboard**:
   - Multi-department filter, SLA countdown timers, geo-tagged photo evidence review.
5. **Anti-Fraud Citizen Confirmation Loop**:
   - Officials must submit **Before & After photos** and action logs.
   - The ticket cannot be marked "Closed" without explicit citizen confirmation. If the citizen disputes the repair, it auto-escalates to the District Magistrate.

---

## 🚀 Quick Start

Run the zero-dependency local server:

```bash
cd GramSetu_Clean
python app.py
```

Before starting, create a local `.env` file from `.env.example` and put your Gemini key there. The `.env` file is ignored by Git. Never paste API keys into source code, screenshots, issues, or commits.

Then open your browser at:
`http://localhost:8080`

---

## 📂 Project Structure

- `app.py`: REST API & static web server (Python standard library).
- `ai_classifier.py`: Domain classification, urgency scoring, SLA calculator.
- `database.py`: SQLite persistence layer with sample pre-seeded rural complaints.
- `public/index.html`: Modern, responsive citizen & official web application.
- `grievances.db`: Local SQLite database.


## 🔐 OTP Authentication

GramSetu now includes passwordless citizen authentication using a 6-digit OTP.

### Local development

The project remains dependency-free and uses SQLite.

```bash
python3 app.py
```

Open:
`http://localhost:8080`

In development mode (`GRAMSETU_ENV` is not `production`), the generated OTP is returned to the browser so the flow can be tested without an SMS provider. The OTP is **hashed before being stored** in SQLite and expires after 5 minutes.

The database contains:

- `users` — citizen profile/login records
- `otp_codes` — hashed, expiring OTP records
- `sessions` — hashed session tokens

No citizen password is stored.

### Production SMS delivery

Set:

```bash
export GRAMSETU_ENV=production
export GRAMSETU_OTP_SECRET="replace-with-a-long-random-secret"
export GRAMSETU_SMS_WEBHOOK_URL="https://your-sms-service.example/send"
```

The SMS webhook receives a JSON payload containing the destination phone number and OTP message. Connect this hook to a real SMS provider or your own secure SMS gateway.

**Do not run production with the development OTP response enabled.**

The browser receives an `HttpOnly` session cookie after successful verification, rather than storing the session token in localStorage.


## 🔐 Complaint privacy after logout

- Complaint list/detail API endpoints require a valid server-side session.
- Citizen accounts can only retrieve reports associated with their own verified phone number; administrator access remains role-protected.
- Logging out revokes the server-side session, clears the session cookie, removes locally cached report data, and returns the browser to Home.
- Complaint tracking is login-gated in the frontend. Do not treat client-side navigation or local storage as an authorization boundary; the API enforces access on every request.

## 🛡️ Developer / Admin Dashboard

GramSetu now includes a server-protected administration console at the same web app.

### Features

- Live SQLite complaint statistics
- Low / Medium / High / Emergency Escalation priority distribution
- AI category distribution
- Complaint search and filters by status, priority, category and district
- Citizen/user directory
- Complaint status and priority updates
- SQLite table/row summary
- Admin audit log
- Authorized CSV export
- Server-side role enforcement on every `/api/admin/*` endpoint

### Create the first admin

For local development, explicitly configure an admin phone before starting the server:

```bash
export GRAMSETU_ADMIN_PHONE="9999999998"
python3 app.py
```

Multiple administrators can be configured with comma-separated numbers:

```bash
export GRAMSETU_ADMIN_PHONE="9999999998,9876543210"
```

The configured number is promoted to the `admin` role at database initialization. If the account does not exist yet, GramSetu creates an admin account for that phone. The administrator still signs in using the normal OTP flow.

**Important:** Do not expose an unrestricted "make me admin" endpoint. Admin promotion is deliberately controlled by server configuration.

### Admin access

After OTP login with an admin account, an **Admin** button appears in the navigation. The dashboard is also protected server-side, so changing the URL or calling the API directly does not grant access.

Protected endpoints include:

- `GET /api/admin/stats`
- `GET /api/admin/complaints`
- `GET /api/admin/users`
- `GET /api/admin/filters`
- `GET /api/admin/database`
- `GET /api/admin/audit`
- `GET /api/admin/export/complaints`
- `POST /api/admin/complaints/<id>`

The dashboard never exposes OTP hashes or session tokens.


## 🤖 Stage 1 AI Complaint Analysis

GramSetu now supports an optional Gemini-powered analysis layer for every new complaint.

The workflow is:

```text
Citizen complaint
      ↓
Save original complaint
      ↓
Google Gemini API (`generateContent`)
      ↓
Structured analysis
      ↓
Save AI analysis in SQLite
      ↓
Admin dashboard
```

The AI extracts:

- Category
- Problem type
- Affected resource
- Scope
- Duration
- Explicitly stated affected-person count
- Severity
- Confidence score

The AI is instructed not to invent missing facts. The original citizen complaint is never overwritten.

### Configure Gemini

Create a Gemini API key in Google AI Studio, then configure it on the server:

```bash
export GEMINI_API_KEY="your_gemini_api_key_here"
export GEMINI_MODEL="gemini-3.5-flash-lite"
python3 app.py
```

**Never put `GEMINI_API_KEY` in `public/index.html` or browser JavaScript.**

If `GEMINI_API_KEY` is not configured, GramSetu automatically uses its existing local rule-based classifier instead. This means the application still runs without an AI account.

The current integration uses Google's Gemini `generateContent` REST API with structured JSON output. The API key is sent only from the GramSetu backend using the `x-goog-api-key` header.

### Where to see the result

1. Log in with your configured administrator account.
2. Open **Admin**.
3. Go to **Complaint database**.
4. Click **AI** next to a newly submitted complaint.
5. The dashboard displays the structured analysis, provider/model, confidence, and fallback status.

### Important architecture rule

Stage 1 is intentionally limited to **complaint understanding and structured extraction**.

It does **not** yet decide that two complaints are the same underlying problem. It does **not** implement semantic embeddings or problem clustering. Those are Stage 2 features.

AI output is advisory. Official staff remain responsible for the final government decision.

### Cost

Gemini has free-tier quotas for supported models, including `gemini-3.5-flash-lite`; quotas and limits can change, so check Google AI Studio before production use. The local fallback costs nothing. You can develop and test the rest of GramSetu without configuring a Gemini key.

## 🧩 Stage 2 AI Underlying Problem Clustering

Stage 2 groups differently worded complaints that likely describe the same real-world problem. It uses Stage 1 structured fields plus complaint meaning and location. When Gemini is configured, Gemini chooses between candidate clusters or a new cluster; ambiguous matches are kept separate rather than being forced together.

The backend stores clusters in `problem_clusters` and complaint membership in `problem_cluster_members`. The admin dashboard includes a **Problem Clusters** tab and a per-complaint **Cluster** action.

Stage 2 is advisory. It does not change official priority and does not make the Stage 3 government decision.

## 🔗 Similar Reports — Stage 2

The admin dashboard includes a dedicated **Similar Reports** tab. It is visible even when there are zero clusters. With no grouped reports, the page shows an empty state; after complaints are submitted, Stage 2 groups complaints that appear to describe the same underlying real-world problem.

Stage 2 is run automatically after a complaint is saved, and administrators can inspect groups from **Similar Reports**.

### Local configuration

You can create a `.env` file beside `app.py`. This build loads it automatically, so you do not need to export variables manually for local testing.

```env
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash-lite
GRAMSETU_ADMIN_PHONE=9999999998
GRAMSETU_ENV=development
```

Start with `python app.py` and open `http://localhost:8080`.


## Stage 2.5 — Evidence Validation

Stage 2.5 validates similar-report clusters with independent evidence such as photos, physical asset consistency, location, time and affected resource. It preserves Stage 2 confidence and stores a separate validated confidence. See `STAGE2_5_AI.md`.


## GitHub and secret handling

- Commit `.env.example`, never `.env`.
- `.gitignore` excludes `.env*` (except `.env.example`) and local SQLite databases.
- `GEMINI_API_KEY` is read by the Python backend only; do not put it in `public/index.html` or any frontend JavaScript.
- For a hosted deployment, add `GEMINI_API_KEY` in the hosting provider's environment/secrets settings.
- If a real API key was ever committed or shared, revoke/rotate it in Google AI Studio before publishing. Removing it in a later commit does not erase it from Git history.
- This ZIP intentionally excludes `grievances.db`, which may contain local or personal complaint data. The app will initialize its database when run.
