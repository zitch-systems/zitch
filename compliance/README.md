# Zitch — Compliance document pack (AML/CFT/CPF & ABC)

Board-approval-ready compliance templates for Zitch Technologies, a VTU / bill‑payment
and wallet platform that delivers regulated functions through licensed partners. Drafted
to the Nigerian framework: **MLPPA 2022, TPPA 2022, CBN AML/CFT/CPF Regulations, NFIU
(goAML), SCUML, and NDPA 2023**.

| File | Purpose |
|---|---|
| `ZITCH_AML_CFT_CPF_POLICY_v1.0.docx` | Core AML/CFT/CPF policy (governance, tiered KYC, screening, monitoring, NFIU reporting, partner oversight, record‑keeping). |
| `ZITCH_ANTI_BRIBERY_CORRUPTION_POLICY_v1.0.docx` | Anti‑Bribery & Corruption policy (questionnaire Section VI). |
| `ZITCH_AML_ABC_QUESTIONNAIRE_RESPONSE_v1.0.docx` | Completed partner due‑diligence questionnaire (Q1–32), cross‑referenced to the policies. |
| `ZITCH_MLRO_APPOINTMENT_LETTER_v1.0.docx` | Board appointment of the Compliance Officer / MLRO (Q2). |
| `ZITCH_CODE_OF_CONDUCT_AML_ATTESTATION_v1.0.docx` | One‑page Code of Conduct AML/ABC staff acknowledgement (Q9). |
| `ZITCH_KYC_CDD_OPERATING_PROCEDURE_v1.0.docx` | Step‑by‑step KYC / CDD SOP that sits under the AML policy; matches the in‑app 4‑tier model. |

### Governance & operational policies

| File | Purpose |
|---|---|
| `ZITCH_CUSTOMER_SERVICE_AND_COMPLAINT_POLICY_v1.0.docx` | Service standards + complaint handling, escalation and reporting (CBN consumer protection). |
| `ZITCH_BUSINESS_CONTINUITY_PLAN_v1.0.docx` | BCP: critical services, RTO/RPO, dependencies, disruption scenarios, testing. |
| `ZITCH_RISK_ASSESSMENT_AND_INCIDENT_RESPONSE_PLAN_v1.0.docx` | Enterprise risk assessment + incident response lifecycle and breach notification. |
| `ZITCH_DATA_PROTECTION_POLICY_v1.0.docx` | Internal NDPA 2023 data‑protection policy (complements the customer‑facing Privacy Policy). |
| `ZITCH_ACCESS_MANAGEMENT_AND_MFA_POLICY_v1.0.docx` | Least‑privilege access lifecycle + mandatory MFA for privileged/remote access. |
| `ZITCH_ATTESTATION_THIRD_PARTY_VENDORS_v1.0.docx` | Attestation letter — Zitch **does** engage third‑party vendors (with vendor categories). |
| `ZITCH_ATTESTATION_NO_SECURITY_BREACH_12_MONTHS_v1.0.docx` | Attestation letter — no security breach in the preceding 12 months (management representation). |

The customer‑facing **Privacy Policy v2.1** and **Terms of Use v2.1** (updated for the
4‑tier KYC model and current security controls) live at the repository root alongside the
prior v2.0 versions.

The policies embed Zitch's actual in‑app controls: a server‑derived 4‑tier KYC model
and per‑tier limits —

| Tier | Requirements (cumulative) | Per‑txn | Daily transfer | Daily bill/VTU |
|---|---|---|---|---|
| 0 — Unverified | email + phone | ₦20,000 | ₦20,000 | ₦10,000 |
| 1 — Verified | + BVN + NIN | ₦50,000 | ₦50,000 | ₦20,000 |
| 2 — Enhanced | + face + address | ₦200,000 | ₦1,000,000 | ₦100,000 |
| 3 — Premium | + government ID document | ₦5,000,000 | ₦5,000,000 | ₦500,000 |

— a ₦100,000 facial step‑up (a Tier 2 requirement), the transaction PIN with lockout,
and BVN/NIN hashing at rest.

## Before use — required steps
1. **Complete every `[ ]` placeholder**: MLRO / ABC‑officer names + contacts, effective
   date, SCUML / CAC / RC numbers, gifts threshold, and the Q11 penalties representation.
2. **Board approval**: have the Board (or delegated committee) adopt the policies; capture
   signatures on the approval blocks.
3. **Legal/compliance review**: qualified Nigerian counsel should confirm the controls match
   Zitch's current licensing and partner contracts before relying on them with a regulator
   or counterparty. These templates are not a substitute for professional advice.
4. **PDF**: export from Word / Google Docs as needed for submission.

_Status: v1.0 drafts for approval. Not legal advice._
