# Gram Setu — Sample Data and Test Cases

These are proposed manual test cases and demo records for the Gram Setu hackathon submission. Use fictional data only. Run them against the current build and update each result to **PASS** or **FAIL** after testing; expected results are not proof that a test has already passed.

## 1. Sample grievance scenarios

Use these complaint descriptions when creating test grievances through the app. The affected-person counts below are included only when explicitly stated in the complaint.

| ID | Sample complaint | Expected category | Useful checks |
|---|---|---|---|
| SG-01 | “For the last two days, the hand pump near the primary school in Rampur has no water. Around 35 families use it.” | Drinking Water | Category extraction; duration; affected-person/family count must not be invented or silently changed |
| SG-02 | “There has been no electricity in Ward 3 since yesterday evening. The whole lane is affected.” | Electricity | Category and duration extraction; avoid inventing an exact number of people |
| SG-03 | “A large pothole has been on the road between Rampur market and the bus stop for three weeks. Two-wheelers have difficulty passing.” | Roads & Potholes | Road category; location and duration extraction |
| SG-04 | “Garbage has not been collected near the community hall for five days, and there is a bad smell.” | Sanitation & Waste | Sanitation category; duration extraction |
| SG-05 | “Water is not reaching the fields through the village irrigation channel, and the crops are drying.” | Agriculture & Irrigation | Agriculture/irrigation category; do not infer crop loss amount |
| SG-06 | “The primary health centre has been closed during its posted opening hours on two occasions this week.” | Healthcare & PHC | Healthcare category; preserve the complaint without adding unsupported medical claims |
| SG-07 | “My approved government benefit has not arrived in my bank account. I checked the status yesterday.” | Govt Schemes & DBT | Scheme/DBT category; do not invent a scheme name or payment amount |
| SG-08 | “The streetlight beside the village library is not working.” | Depends on classifier rules | Check whether the app asks for clarification or assigns a reasonable category; do not require an exact AI wording |
| SG-09 | “There is a problem in the village.” | Unclear / needs clarification | Missing details should not be fabricated; check validation or low-information handling |
| SG-10 | “The water supply is still broken near the primary school in Rampur; the same hand pump has not worked for two days.” | Drinking Water | Submit alongside SG-01 and inspect whether Stage 2 identifies a possible related issue without merging unrelated complaints |

**Demo-data note:** Rampur and all descriptions above are fictional examples. Do not use real citizens’ phone numbers, identity details, or sensitive complaint records in a public demo.

## 2. Manual functional test cases

| Test ID | Feature | Steps | Expected result | Actual result |
|---|---|---|---|---|
| TC-01 | Application startup | Follow README setup instructions and start the server. | App starts without a fatal error and loads at `http://localhost:8080`. | Not run |
| TC-02 | Citizen OTP — development | Sign in using a test phone number in development mode and use the generated OTP. | Valid, unexpired OTP permits login; invalid OTP is rejected. | Not run |
| TC-03 | OTP expiry | Request an OTP, wait beyond its 5-minute validity, then try it. | Expired OTP is rejected. | Not run |
| TC-04 | Complaint registration | Submit SG-01 with required fields. | Complaint is saved and a unique ID in the documented `GRV-2026-NNNNNN` format is displayed, subject to the app’s current ID-generation logic. | Not run |
| TC-05 | Required-field validation | Try submitting a complaint with required fields missing. | App shows a validation message and does not save an invalid complaint. | Not run |
| TC-06 | Stage 1 AI analysis | Configure a valid Gemini key on the server, submit SG-01, and inspect the analysis in Admin. | Analysis provider/status indicates Gemini rather than fallback; category and extracted fields are grounded in the complaint text. | Not run |
| TC-07 | AI unavailable fallback | Remove or unset the Gemini key, restart the app, and submit a test complaint. | App remains usable and reports rule-based fallback; it does not claim Gemini performed the analysis. | Not run |
| TC-08 | No invented facts | Submit SG-08 or SG-09 and inspect analysis. | Unknown details remain unknown or are flagged as unclear; unsupported counts, dates, and locations are not invented. | Not run |
| TC-09 | Stage 2 similar reports | Submit SG-01 and SG-10, then inspect Similar Reports / clusters. | System may group them as a likely shared issue; confirm the result in the UI. Unrelated reports should not be forced into the same cluster. | Not run |
| TC-10 | Stage 2.5 evidence validation | Open a proposed cluster and review available location, time, resource, and evidence information. | Validation result is separate from the original clustering confidence; missing evidence is not represented as verified evidence. | Not run |
| TC-11 | Complaint privacy | Sign in as one test citizen and attempt to view another citizen’s complaint through the UI/API. | Access to another citizen’s private complaint is denied. | Not run |
| TC-12 | Logout/session revocation | Log in, log out, then try to reuse the old session. | Session is no longer accepted and protected complaint data is not available. | Not run |
| TC-13 | Admin authorization | Open an admin endpoint while signed out or logged in as a normal citizen. | Access is denied; changing the URL alone does not grant admin rights. | Not run |
| TC-14 | Admin filters | Log in as an authorized admin and filter complaints by category, status, priority, or district where available. | Results reflect the selected filters. | Not run |
| TC-15 | Citizen confirmation before closure | As admin, attempt to close a complaint without the required citizen confirmation. | Complaint cannot be marked closed until the configured confirmation flow is completed. Verify current build behavior. | Not run |
| TC-16 | Disputed resolution | In a test record, follow the citizen dispute flow after a proposed repair. | If implemented in the current build, the dispute is recorded and escalated as documented. Do not claim this test passed without running it. | Not run |
| TC-17 | Secret handling | Check tracked files and browser assets for a real API key. | No real `.env` file or Gemini key is committed or exposed in frontend code. | Not run |
| TC-18 | Stage 3 future scope | Review the demo and documentation. | Grievance prioritization by severity, urgency, and affected population is clearly labelled future scope if it is not implemented. | Not run |

## 3. Suggested demo sequence

1. Start the application and sign in with a development test account.
2. Submit SG-01 and note its generated grievance ID.
3. Inspect its AI analysis and clearly show whether the result came from Gemini or the rule-based fallback.
4. Submit SG-10 and inspect the Similar Reports / clustering view.
5. Demonstrate the authorized admin dashboard and, where available, evidence validation.
6. Show privacy/access-control checks using separate test accounts.
7. Describe Stage 3 prioritization as future scope unless it has been implemented and tested.

## 4. Reporting results

Before submitting, run the test cases on the version you will demo. Replace **Not run** with **PASS**, **FAIL**, or **BLOCKED**, and add a short note for failures or environment-dependent cases. Do not label unexecuted tests as passed.

**Environment details to record**
- Commit/version tested:
- Operating system and Python version:
- Gemini configured: Yes / No
- Test date:
- Tester:
