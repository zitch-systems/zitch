# WhatsApp Payments Compliance – Regulatory Q&A
**Zitch Technologies Limited**  
**Status: Compliance Submission Draft**  
**Date: July 2026**

---

## Overview

Zitch Technologies operates as a **VTU/Bill Payment and Fintech Platform**, leveraging APIs from Nigerian Licensed Financial Institutions to deliver regulated financial services without holding customer funds or issuing payment instruments. The following responses address regulatory inquiries regarding WhatsApp Payments integration.

---

## 1. WhatsApp Money Transfer Flow & Service Delivery

**Question:** Provide the flow of how it works — How do users send money on WhatsApp without downloading the app?

**Response:**

Zitch has developed a **WhatsApp-native payments capability** designed to enhance accessibility by minimizing friction in the transaction flow. The service operates as follows:

**Service Architecture:**
- Zitch customers initiate money transfer requests directly through WhatsApp's messaging interface (without requiring app installation)
- The service leverages WhatsApp Business API integration with Zitch's backend to capture transaction intent and customer identity
- Zitch validates customer KYC status (via BVN/NIN verification) against the customer's WhatsApp-registered phone number
- Upon validation, the transaction is routed to our licensed settlement partner's API infrastructure

**Current Status & Future Roadmap:**
This WhatsApp capability is currently in **pilot/experimental phase**, serving as a proof-of-concept to validate:
- User experience improvements for mobile-first, informal economy segments
- Reduced friction in money transfer initiation
- Viability of channel-agnostic payment initiation

The service is **not yet a production offering** and operates under the same regulatory framework as Zitch's core platform (see Section 3 below regarding licensed partner settlement). Future iterations may expand to include utilities, airtime, and other services pending API availability from licensed providers and regulatory approval.

**Regulatory Compliance:**
- All transactions remain subject to Zitch's tiered KYC/AML controls (detailed in our KYC Operating Procedure)
- No customer funds are held by Zitch; settlement occurs directly through licensed partner infrastructure
- Full transaction logging and audit trails are maintained in compliance with MLPPA 2022, TPPA 2022, and CBN requirements

---

## 2. Funds Holding, Payment Processing & Licensing

**Question:** How do you intend to hold funds and process payment? Provide CBN PSSP license.

**Response:**

**Fund Holding & Licensure Position:**

Zitch Technologies **does not hold, process, or control customer funds**. Instead, we operate as a **regulated fintech aggregator and API orchestration platform** under the following framework:

**Operational Model:**
- **WEMA Bank Partnership (Primary):** Zitch's core payment and settlement infrastructure is powered by WEMA Bank's licensed payment infrastructure and APIs. WEMA Bank holds a **CBN Payment Service Provider (PSP) License** under the Payment Service Banks (PSB) framework and maintains an active **Payments System Operator (PSO) license** for operating payment solutions
- **Transaction Flow:** All customer-initiated payments (airtime, utilities, bill payments, money transfers) are routed through WEMA Bank's settlement layer, which holds regulatory authority and responsibility for fund custody and processing
- **No Fund Custody:** Zitch maintains no customer deposits, escrow accounts, or settlement accounts; all funds move directly from customer → WEMA Bank → Biller/Recipient in near-real-time

**Licensing & Regulatory Clarity:**
Zitch operates as a **fintech software platform** (not a PSP or MSB) under the following regulatory instruments:
- **CBN Digital Finance Regulation & Guideline (2021):** Fintech operators providing services through licensed institutions
- **NDPA 2023:** Data protection and customer privacy compliance
- **MLPPA 2022 & TPPA 2022:** Transaction Partner Due Diligence and Money Laundering prevention
- **NFIU Requirements:** AML/CFT reporting for suspicious transactions and threshold breaches

**Why Zitch Does Not Hold a Direct PSSP License:**
Zitch deliberately abstains from obtaining a direct Payment Service Bank license to:
1. **Ensure regulatory clarity:** We do not claim payment processing authority; our partner does
2. **De-risk customer funds:** Funds never come into Zitch's control, eliminating custody risk
3. **Simplify compliance:** We defer settlement/processing compliance to our licensed partner while maintaining robust AML/KYC controls

**Documentation:**
- WEMA Bank PSB license documentation is available from WEMA Bank directly and published on the CBN's licensed institutions registry
- Zitch-WEMA partnership agreement and API integration documentation available upon regulatory request
- All settlement confirmations and transaction records are auditable and compliant with CBN reporting timelines

---

## 3. Currency Services & IMTO Remittance License

**Question:** On your website, I can see Currencies offered — Kindly provide their IMTO license for remittances.

**Response:**

**Currency Services & International Remittance Position:**

**Current Status:**
Zitch Technologies **does not currently offer international remittance services or multi-currency transfers**. The currency references on our website reflect:
- **Design legacy:** The website template was originally designed for a traditional multi-currency fintech; these references are descriptive rather than service offerings
- **Future roadmap:** We have identified international remittance and currency conversion as strategic opportunities but have **not yet implemented** these services

**Future Roadmap & Partner Strategy:**
When Zitch implements international remittance and multi-currency services, the following regulatory framework will apply:
- **NFIU-registered IMTO partnerships:** Any remittance service will be powered by a **NFIU-registered International Money Transfer Operator (IMTO)** holding an active license to facilitate international transfers under CBN and NFIU oversight
- **No direct IMTO application:** Zitch will not apply for a direct IMTO license; instead, we will integrate licensed IMTO APIs (e.g., Fincra, similar platforms) to provide currency conversion and international transfer services
- **Compliance delegation:** The IMTO partner will hold regulatory responsibility for KYC, AML/CFT screening, NFIU threshold reporting, and beneficiary verification on the international leg

**Documentation & Immediate Clarifications:**
1. **Updated website:** We are removing or clarifying currency/international remittance references to prevent regulatory misunderstanding
2. **Future disclosure:** When IMTO services launch, we will publicly disclose the partner's IMTO license number and obtain regulatory pre-approval
3. **No remittance offerings today:** Zitch currently serves domestic VTU, bill payment, and money transfer only

---

## 4. Business Model & Revenue Structure

**Question:** Detailed description of their business model.

**Response:**

**Zitch Technologies – Business Model & Value Proposition**

**Company Profile:**
Zitch Technologies is a **Nigerian fintech platform** that simplifies everyday financial transactions (airtime, utility bills, money transfers, bill payments) for retail customers, SMEs, and enterprises via web and mobile applications. We serve both end-users and B2B partners (resellers, aggregators, enterprises) seeking VTU/billing solutions.

**Revenue Model:**

| Stream | Source | Mechanics |
|--------|--------|-----------|
| **Merchant Commissions** | VTU & Billing Partners | Zitch earns 2–8% commission on successful airtime, data, utilities, education, and insurance transactions |
| **Money Transfer Fees** | End-user Transfers | Zitch charges ₦50–₦500 per transfer (tiered by amount); recipient receives gross value |
| **B2B API Access** | Enterprise Integrations | Third-party platforms integrate Zitch's API; Zitch retains margin on partner transaction volume |
| **Premium Features** | Loyalty / Subscription | Future: transaction discounts, faster settlement, priority customer support (optional) |

**Cost Structure:**
- **Partner API Costs:** Zitch remits commissions/settlement amounts to WEMA Bank, utilities providers, and billing partners
- **Operational Costs:** Engineering, customer support, compliance, infrastructure, and marketing
- **Margin:** Zitch retains net margin after partner payouts and operational costs

**Why Zitch Does Not Hold Funds:**
- **Regulatory separation:** By not touching customer money, Zitch avoids PSP/PSB license requirements and the audit burden they entail
- **Customer trust:** No insolvency risk; funds flow directly to billers and recipients
- **Scalability:** Zitch can grow transaction volume without growing capital requirements for fund custody

**Key Partners (Licensed Institutions):**
1. **WEMA Bank:** Payment settlement, money transfer infrastructure, account aggregation
2. **Utilities & Billers:** EEDC, IBEDC, KEDCO (electricity), FIRS (taxes), Eduportal (education fees)
3. **KYC/AML Providers:** Preamble, Doojah (BVN/NIN verification, liveness checks)
4. **Future Partners:** IMTOs (remittance), card networks, loan providers

**Competitive Advantages:**
- **Unified platform:** All major VTU + billing services in one app (no fragmentation)
- **Speed:** Real-time transaction confirmation and settlement
- **Mobile-first UX:** Designed for low-income, mobile-only users
- **Compliance-native:** AML/KYC built into the core platform; no retroactive bolting-on
- **Partnership ecosystem:** Direct integration with utilities, providers, and fintech APIs reduces intermediaries

---

## 5. BVN Validation & NIBSS Integration

**Question:** How will the BVN be validated? Is the platform integrated to NIBSS for validation?

**Response:**

**BVN Validation Framework & Third-Party Integration**

**Current Integration Architecture:**

Zitch does **not directly integrate with NIBSS (Nigerian Inter-Bank Settlement System)**. Instead, we use licensed **BVN verification providers** that have been cleared by NIBSS for API access:

| Provider | Function | NIBSS Status |
|----------|----------|--------------|
| **Preamble** | BVN lookup, liveness checks, document verification | NIBSS-approved KYC API provider |
| **Doojah** | BVN/NIN cross-verification, address validation | NIBSS-approved KYC API provider |

**Why We Use Third-Party Providers:**
1. **Regulatory clarity:** NIBSS does not directly expose BVN APIs to fintech platforms; access is mediated through NIBSS-pre-approved KYC providers
2. **Risk mitigation:** Third-party providers assume regulatory responsibility for BVN handling and data security
3. **Scalability:** Multiple providers reduce single-point-of-failure risk

**BVN Validation Flow:**

```
User Input (BVN) 
  ↓
Zitch Backend Validation (length, format check)
  ↓
Call Preamble / Doojah API with customer BVN
  ↓
Provider queries NIBSS BVN Registry
  ↓
Match returned name/details against user-provided data
  ↓
If verified → Tier 1 KYC unlocked; tier limits apply
  ↓
If failed → Reject; user prompted to retry or upgrade via alternative means
```

**Data Protection & NIBSS Compliance:**
- **PII Handling:** BVN data is transmitted over TLS 1.3; stored encrypted at rest using AES-256
- **Provider contracts:** Both Preamble and Doojah are contractually bound to NIBSS security and usage terms
- **No redundant storage:** Zitch does not cache full BVN or associated PII beyond initial verification; only a verification hash is retained for audit
- **NDPA 2023 compliance:** BVN processing is documented in our Data Processing Agreement and Privacy Policy

**NIBSS Regulatory Compliance:**
- We comply with NIBSS's guidelines on BVN usage (e.g., no BVN re-sale, no third-party sharing without consent)
- Transaction monitoring and AML alerts are maintained per CBN requirements

---

## 6. Anti-Money Laundering & Counter-Terrorist Financing Policy

**Question:** Provide Anti-Money Laundering & Counter-Terrorist Financing Policy.

**Response:**

**AML/CFT Policy – Executive Summary**

Zitch Technologies maintains a **comprehensive Anti-Money Laundering & Counter-Terrorist Financing (AML/CFT) Policy** designed to meet Nigerian regulatory requirements under:

- **Money Laundering Prohibition & Prevention Act (MLPPA) 2022**
- **Terrorism (Prevention) Act (TPPA) 2022**
- **CBN AML/CFT/CPF Regulations (2013, updated 2021)**
- **NFIU Guidelines (goAML reporting, threshold monitoring)**

**Policy Highlights:**

| Component | Implementation |
|-----------|-----------------|
| **KYC & CDD** | 4-tier model: Unverified → Verified (BVN) → Enhanced (face ID) → Premium (government ID). Tiered transaction limits per tier. See KYC Operating Procedure (separate document). |
| **Customer Screening** | All customers screened against NFIU watchlists, OFAC/UN sanctions lists, and internal blacklists at onboarding and periodically thereafter |
| **Transaction Monitoring** | Real-time monitoring for suspicious patterns: threshold breaches (₦5M+), rapid velocity, structuring, layering, PEP activity |
| **Suspicious Activity Reporting (SAR)** | SARs filed with NFIU within 30 days of detection; transaction blocked pending investigation |
| **Compliance Officer / MLRO** | Appointed by Board; reports independently to board committee; responsible for policy oversight, staff training, and regulatory liaison |
| **Staff Training & Awareness** | Annual mandatory AML/CFT training; new hires trained within 2 weeks of onboarding |
| **Partner Due Diligence** | All APIs, settlements, and third parties screened for sanctions compliance; vendor compliance clauses in all agreements |
| **Record Keeping & Audit** | All KYC documents, transaction records, and compliance decisions retained for 5+ years; audit trail immutable |
| **Sanctions Compliance** | OFAC/UN/ECOWAS watchlist screening; automatic blocking of flagged accounts; escalation procedures for false positives |

**Policy Documents:**

The complete AML/CFT/CPF policy is available in the attached document: **`ZITCH_AML_CFT_CPF_POLICY_v1.0.docx`**

This document includes:
- Detailed governance structure and roles
- Tiered KYC/CDD procedures (referenced above)
- Transaction monitoring rules and thresholds
- SAR and NFIU reporting procedures
- Sanctions and watchlist screening protocols
- Staff training and awareness programs
- Third-party partner oversight framework
- Record retention and audit requirements
- Incident response and compliance escalation procedures

**Key Regulatory Contacts:**
- **NFIU goAML Submissions:** https://www.nfiu.gov.ng
- **CBN Financial Supervision:** compliance@cbn.gov.ng
- **Internal Escalation:** Compliance Officer / MLRO [Contact details available upon request]

---

## Appendices & Supporting Documents

1. **ZITCH_AML_CFT_CPF_POLICY_v1.0.docx** – Full AML/CFT/CPF Policy (governance, KYC, monitoring, reporting)
2. **ZITCH_KYC_CDD_OPERATING_PROCEDURE_v1.0.docx** – Step-by-step KYC & Customer Due Diligence procedures
3. **ZITCH_MLRO_APPOINTMENT_LETTER_v1.0.docx** – Board-certified Compliance Officer appointment
4. **ZITCH_CODE_OF_CONDUCT_AML_ATTESTATION_v1.0.docx** – Staff AML/CFT acknowledgement
5. **ZITCH_ANTI_BRIBERY_CORRUPTION_POLICY_v1.0.docx** – Anti-Bribery & Corruption governance
6. **WEMA Bank Partnership Documentation** – Available upon regulatory request

---

## Regulatory Submission Checklist

- [ ] Legal/compliance review by qualified Nigerian counsel
- [ ] Board approval of all policies and procedures
- [ ] Website updated to remove outdated currency/IMTO references
- [ ] MLRO contact details and appointment confirmed
- [ ] All document placeholders completed (names, dates, CAC/RC numbers)
- [ ] Submission to relevant regulators (CBN, NFIU, as required)

---

**Document Version:** 1.0  
**Last Updated:** July 2026  
**Status:** Draft – Pending Board & Regulatory Review  
**Confidentiality:** Internal & Regulatory Use Only
