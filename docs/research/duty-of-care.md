# Research: Passenger Duty of Care & Regulatory Frameworks (GRU → SCL Maintenance Disruption)

> **Context:** GitHub Issue [#31](https://github.com/duboc/acme-airlines/issues/31) · Feeds Policy Module [#5](https://github.com/duboc/acme-airlines/issues/5)  
> **Topic:** Duty of care, assistance thresholds, and compensation regimes for an unscheduled maintenance cancellation on flight AC1234 (São Paulo GRU → Santiago SCL).  
> **Status:** Completed Research Document  
> **Disclaimer:** This document is prepared for software engineering policy specification (`duty_of_care.py`) in the ACME Airlines software factory and does not constitute formal legal advice.

---

## 1. Executive Summary

When flight **AC1234** (GRU → SCL) is cancelled at 22:03 due to unscheduled maintenance:
1. **Primary Governing Law:** **Brazilian Law (ANAC Resolution 400/2016)** exclusively governs the departure and initial disruption remedies at São Paulo Guarulhos (GRU). Under ANAC 400, material assistance is strict, progressive by wait time, and non-derogable.
2. **Technical Maintenance Impact:** Maintenance is legally classified under Brazilian jurisprudence as an inherent operational risk (*fortuito interno*, CDC Art. 14). Moreover, ANAC 400 Art. 26 § 3º explicitly bars carriers from suspending material assistance even for force majeure. All duty-of-care obligations are fully enforceable.
3. **EU261/2004 Non-Applicability:** Regulation (EC) No 261/2004 does **not** apply. The flight departs from Brazil (third country) and arrives in Chile (third country) with no EU territorial nexus or Community carrier operation (EU261 Art. 3(1)).
4. **Chilean Aviation Law (JAC / Ley 18.916 / Ley 19.496):** Applies only to segments departing from Chile or to through-ticket connection failures occurring in Santiago (SCL). Rebooking onto morning flight AC1240 (arriving 10:15) preserves Mariana Silva's 14:00 international connection with 225 minutes of margin (well above SCL's 90-minute MCT), preventing downstream Chilean disruption claims.
5. **Mandatory Entitlements for AC1234:**
   - **Communication:** Immediate access (Art. 27, I)
   - **Food:** Dinner voucher at 22:03 + breakfast voucher next morning (Art. 27, II)
   - **Accommodation:** Partner hotel lodging (#HT8842) for overnight delay (Art. 27, III)
   - **Ground Transportation:** Round-trip transfer airport ↔ hotel (Art. 27, III)
   - **Re-accommodation:** Free first-opportunity morning flight (AC1240) preserving onward PNR (Art. 21, I & Art. 28)

---

## 2. Brazilian Regime: ANAC Resolution 400/2016 (Primary Authority)

Brazilian National Civil Aviation Agency (*Agência Nacional de Aviação Civil* - ANAC) Resolution No. 400 of December 13, 2016 establishes general terms for regular domestic and international passenger air transport departing from Brazil.

### 2.1 Scope & Jurisdictional Trigger
- **[Art. 1º](https://www.gov.br/anac/pt-br/assuntos/regulados/empresas-aereas/repositorio-de-respostas-as-demandas-institucionais/resolucao-no-400-de-13-12-2016):** Applies to regular commercial passenger air transportation, domestic and international, operating from Brazilian airports.
- Flight AC1234 departs São Paulo Guarulhos (GRU); therefore, the carrier must comply with ANAC 400 from the moment of disruption.

### 2.2 Information Duties
- **[Art. 20, caput, II](https://www.gov.br/anac/pt-br/assuntos/regulados/empresas-aereas/repositorio-de-respostas-as-demandas-institucionais/resolucao-no-400-de-13-12-2016):** The carrier must immediately inform passengers regarding flight cancellation or service interruption through all available communication channels.
- **[Art. 20, § 1º](https://www.gov.br/anac/pt-br/assuntos/regulados/empresas-aereas/repositorio-de-respostas-as-demandas-institucionais/resolucao-no-400-de-13-12-2016):** In delays, the carrier must update passengers every 30 minutes regarding the expected departure time.
- **[Art. 20, § 2º](https://www.gov.br/anac/pt-br/assuntos/regulados/empresas-aereas/repositorio-de-respostas-as-demandas-institucionais/resolucao-no-400-de-13-12-2016):** The carrier must provide written explanation of the disruption cause upon passenger request.

### 2.3 Material Assistance Thresholds
Under **[Art. 26](https://www.gov.br/anac/pt-br/assuntos/regulados/empresas-aereas/repositorio-de-respostas-as-demandas-institucionais/resolucao-no-400-de-13-12-2016)**, material assistance is triggered by delay, cancellation, service interruption, or denied boarding. Under **[Art. 27](https://www.gov.br/anac/pt-br/assuntos/regulados/empresas-aereas/repositorio-de-respostas-as-demandas-institucionais/resolucao-no-400-de-13-12-2016)**, assistance is progressive and free:

| Wait Time Threshold | Mandatory Entitlement | Legal Citation | Operational Fulfilment |
| :--- | :--- | :--- | :--- |
| **Delay ≥ 1 hour** | Communication facilities | [Art. 27, I](#ref-anac400) | Free internet/Wi-Fi access or phone call capabilities. |
| **Delay ≥ 2 hours** | Food & refreshments | [Art. 27, II](#ref-anac400) | Meal voucher or direct meal appropriate to time of day (dinner/breakfast). |
| **Delay ≥ 4 hours** | Accommodation (hotel) & round-trip transfers | [Art. 27, III](#ref-anac400) | Hotel lodging when an **overnight stay** is needed (*em caso de pernoite*), plus round-trip ground transfer between airport and hotel. |
| **Origin Resident Carve-out** | Transfer home & back only | [Art. 27, § 1º](#ref-anac400) | If passenger resides in the origin airport locality, carrier may waive hotel lodging but **must** provide round-trip home transfer. |
| **PNAE Special Assistance** | Accommodation without overnight requirement | [Art. 27, § 2º](#ref-anac400) | Passengers with special assistance needs (Res. 280/2013) receive accommodation for delays ≥ 4h even if no overnight stay is involved. |

### 2.4 Re-accommodation and Refund Options
Under **[Art. 21](https://www.gov.br/anac/pt-br/assuntos/regulados/empresas-aereas/repositorio-de-respostas-as-demandas-institucionais/resolucao-no-400-de-13-12-2016)** and **[Art. 28](https://www.gov.br/anac/pt-br/assuntos/regulados/empresas-aereas/repositorio-de-respostas-as-demandas-institucionais/resolucao-no-400-de-13-12-2016)**, when a cancellation occurs or delay exceeds 4 hours, the carrier must immediately present passenger choices:
1. **Re-accommodation at earliest opportunity:** Free on carrier's own flight or a third-party airline's flight for the same destination ([Art. 28, I](#ref-anac400)). Re-accommodation on a third-party carrier has zero cost to the passenger ([Art. 28, § 1º](#ref-anac400)).
2. **Re-accommodation at passenger convenience:** On carrier's own flight at a later date chosen by the passenger ([Art. 28, II](#ref-anac400)).
3. **Full refund:** Within 7 days, including all ticket prices and airport boarding fees ([Art. 21, II](#ref-anac400) & [Art. 29](#ref-anac400)).
4. **Precedence:** Re-accommodation of disrupted passengers takes precedence over selling new seats on those flights ([Art. 21, § 1º](#ref-anac400) & [Art. 28](#ref-anac400)).

### 2.5 Technical / Maintenance Causation and Carrier Liability
Does an unscheduled mechanical or maintenance failure excuse the airline from any duty? **No.**
- **Statutory Non-Derogability:** **[Art. 26, § 3º](https://www.gov.br/anac/pt-br/assuntos/regulados/empresas-aereas/repositorio-de-respostas-as-demandas-institucionais/resolucao-no-400-de-13-12-2016)** explicitly states:  
  > *"O transportador não poderá suspender a prestação de assistência material sob alegação de caso fortuito ou força maior."*  
  Even in extraordinary circumstances or severe weather, material assistance cannot be denied.
- **Inherent Operational Risk (*Fortuito Interno*):** Under the Brazilian Consumer Defense Code (*Código de Defesa do Consumidor* - [Lei 8.078/1990, Art. 14](http://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm)) and settled Superior Court of Justice jurisprudence ([STJ REsp 1.584.465/MG](#ref-stj-resp1584465), [AgInt no AREsp 1.849.567/SP](#ref-stj-aresp1849567)), unscheduled aircraft maintenance and mechanical defects are classified as *fortuito interno* (inherent business risks). They do **not** constitute force majeure and do **not** exclude carrier liability.
- **Compensation Eligibility:** Unlike EU261, ANAC 400 does not set fixed administrative lump-sum compensation tables for cancellations (ANAC 400 Art. 10 sets statutory compensation only for denied boarding/overbooking: 250 DES domestic / 500 DES international). However, Brazilian courts grant civil damages (*danos morais e materiais*) under CDC Art. 14 whenever maintenance cancellations cause extended distress or loss of connection without swift duty-of-care resolution.

---

## 3. European Framework: EU Regulation (EC) No 261/2004

### 3.1 Territorial and Jurisdictional Scope Analysis
Regulation (EC) No 261/2004 establishes common rules on passenger assistance and compensation in the European Union. Its scope is defined by **[Article 3(1)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32004R0261)**:
- **Art. 3(1)(a):** Applies to passengers departing from an airport located in the territory of an EU Member State.
- **Art. 3(1)(b):** Applies to passengers departing from a third country to an airport in an EU Member State, provided the operating carrier is a Community (EU) carrier.

**Determination for AC1234 (GRU → SCL):**
- **Departure airport:** São Paulo Guarulhos (GRU) — Brazil (Third Country, non-EU).
- **Arrival airport:** Santiago de Chile (SCL) — Chile (Third Country, non-EU).
- **Carrier:** ACME Airlines (non-EU carrier).
- **Conclusion:** **EU261 does NOT apply.** Neither the origin nor the destination is in EU territory, and the flight operates entirely outside the EU.

### 3.2 CJEU Precedents on Extra-Territoriality
In Case **[C-451/19 *Airhelp Ltd v Scandinavian Airlines System*](https://curia.europa.eu/juris/liste.jsf?num=C-451/19)** and Case **[C-502/18 *CS and Others v České aerolinie*](https://curia.europa.eu/juris/liste.jsf?num=C-502/18)**, the Court of Justice of the European Union (CJEU) affirmed that Regulation No 261/2004 does not apply to flights operated entirely outside the EU between two third-country airports, even if booked as part of a single through-ticket, unless departing directly from an EU airport.

### 3.3 Systematic Comparison: ANAC 400 vs. EU 261/2004

| Dimension | ANAC Resolution 400/2016 (Brazil) | EU Regulation 261/2004 (Europe) |
| :--- | :--- | :--- |
| **Jurisdictional Trigger** | Flights departing Brazilian territory (domestic & international). | Flights departing EU, or arriving into EU on an EU carrier. |
| **Right to Information** | Immediate notification; updates every 30 min; written cause upon request ([Art. 20](#ref-anac400)). | Written notice handed out at check-in / when delay ≥ 2h ([Art. 14](#ref-eu261)). |
| **Communication** | Triggered at **delay ≥ 1 hour** ([Art. 27, I](#ref-anac400)). | Triggered alongside meals: 2 calls, telex/fax, or emails ([Art. 9(2)](#ref-eu261)). |
| **Food & Refreshments** | Triggered at **delay ≥ 2 hours** ([Art. 27, II](#ref-anac400)). | Tiered by distance: 2h (≤1500 km), 3h (1500–3500 km), 4h (>3500 km) ([Art. 9(1)(a)](#ref-eu261)). |
| **Accommodation** | Triggered at **delay ≥ 4 hours + overnight** ([Art. 27, III](#ref-anac400)). | Triggered when stay of 1+ nights becomes necessary ([Art. 9(1)(b)](#ref-eu261)). |
| **Ground Transfers** | Round-trip airport ↔ hotel included ([Art. 27, III](#ref-anac400)). | Transport between airport and lodging included ([Art. 9(1)(c)](#ref-eu261)). |
| **Resident Carve-out** | Hotel waived if resident at origin city; transfer home still required ([Art. 27, § 1º](#ref-anac400)). | No express statutory resident carve-out (airline provides transport). |
| **Special Needs (PNAE)** | Hotel provided for ≥ 4h delay even without overnight ([Art. 27, § 2º](#ref-anac400)). | Priority attention for reduced mobility under [Art. 11](#ref-eu261). |
| **Statutory Cash Compensation** | No fixed administrative tariff for cancellations (civil damages under CDC Art. 14). Overbooking only: 250/500 DES ([Art. 10](#ref-anac400)). | Fixed statutory cash compensation (€250 / €400 / €600) for cancellation/long delay ([Art. 7](#ref-eu261)). |
| **Force Majeure Defense** | Material assistance **cannot** be suspended under any circumstance ([Art. 26, § 3º](#ref-anac400)). | Excuses Art. 7 cash compensation; **never** excuses Art. 9 care ([McDonagh C-12/11](#ref-cjeu-mcdonagh)). |
| **Technical Maintenance** | *Fortuito interno* — inherent operational risk; full carrier liability ([STJ REsp 1.584.465](#ref-stj-resp1584465)). | Not extraordinary circumstances; no defense against compensation ([C-549/07 Wallentin-Hermann](#ref-cjeu-wallentin)). |

---

## 4. Chilean Framework: Código Aeronáutico & Ley 19.496 (JAC / SERNAC)

### 4.1 Applicable Statutes
- **[Código Aeronáutico (Ley N° 18.916)](https://www.bcn.cl/leychile/navegar?idNorma=30287):** Title VIII, Chapter V, Paragraph 1 (Articles 133 A through 133 F), introduced by **Ley N° 20.831 (2015)** and amended by **Ley N° 21.392 (2021)**:
  - **Art. 133 A:** Mandates care assistance during delays and cancellations: communication facilities (> 1h), meals and snacks (> 2h, minimum 0.5 UF value), and lodging plus ground transportation if overnight stay is required.
  - **Art. 133 B:** Grants passengers the right to rerouting on next available flight, assistance, refund, and statutory indemnification if the cancellation is attributable to the carrier.
  - **Art. 133 C:** Mandates mandatory refund of airport boarding taxes (*tasas de embarque*) for untraveled flights.
- **[Ley N° 19.496 (Consumer Rights)](https://www.bcn.cl/leychile/navegar?idNorma=61438):** Administered by SERNAC (*Servicio Nacional del Consumidor*); Articles 12 and 23 enforce strict contractual compliance and safety in service provision.

### 4.2 Application to Arriving and Connecting Passengers at SCL
- **International Conflict of Laws:** The formation, performance of departure, and initial cancellation of AC1234 are seated in São Paulo, Brazil; hence ANAC 400 governs the immediate overnight care and rebooking at GRU.
- **Santiago Connection Impact:** Chilean aviation consumer rules apply once the passenger arrives in Chile or for any connecting flight departing Arturo Merino Benítez Airport (SCL).
- **Connection Preservation:** In Mariana's scenario, Mariana has an onward international flight departing SCL at 14:00 the following day. By rebooking her onto AC1240 (scheduled arrival 10:15 SCL), the carrier provides **225 minutes of connection margin** (minimum connection time at SCL is 90 minutes). Because the connection is successfully preserved, no Chilean misconnection penalties or onward delay claims under Art. 133 B are triggered.

---

## 5. Scenario Application: AC1234 Disruption (Mariana Silva)

### 5.1 Factual Baseline
- **Flight:** AC1234 (GRU → SCL), scheduled evening departure ~22:30.
- **Cancellation Event:** Published by OCC at 22:03 due to unscheduled technical aircraft maintenance.
- **Passenger:** Mariana Silva — ACME Frequent Flyer **Diamond** tier, traveling with 1 checked bag.
- **Downstream Constraint:** International onward connection in Santiago (SCL) at 14:00 tomorrow.
- **Automated Rebooking:** Morning nonstop AC1240 (departing GRU 08:15, arriving SCL 10:15).
- **Net Waiting Time:** ~10 hours 12 minutes (overnight wait in São Paulo).

### 5.2 Mandatory ANAC 400 Entitlements Evaluation

```
Disruption: Cancellation (AC1234 @ 22:03)
Cause: Unscheduled Technical Maintenance (Fortuito Interno)
Wait Time: 10h 12m (Overnight)
Origin: GRU (Brazil) ──► Governing Regime: ANAC 400/2016
  │
  ├── Art. 20 ────────► Immediate Proactive WhatsApp Notice
  ├── Art. 27, I ─────► Free Wi-Fi / Connectivity Pass
  ├── Art. 27, II ────► Dinner Voucher (Night) + Breakfast Voucher (Morning)
  ├── Art. 27, III ───► Partner Hotel #HT8842 (Overnight Accommodation)
  ├── Art. 27, III ───► Round-Trip Ground Transfer (GRU ◄─► Hotel)
  ├── Art. 28, I ─────► Earliest Flight AC1240 @ 08:15 (Free Re-accommodation)
  └── CDC Art. 14 ────► Compensation Flag = Eligible (Mitigated by Proactive Care)
```

1. **Information Duty ([Art. 20](#ref-anac400)):** Fulfilled backstage at 22:09 via empathetic WhatsApp notification detailing the complete recovery itinerary.
2. **Communication Facilities ([Art. 27, I](#ref-anac400)):** Required (delay > 1h). Digital data/Wi-Fi pass issued.
3. **Meal Vouchers ([Art. 27, II](#ref-anac400)):** Required (delay > 2h). Because the delay spans overnight, Mariana is entitled to a dinner voucher at the airport/hotel at 22:03 and breakfast next morning prior to the 08:15 departure.
4. **Hotel Accommodation ([Art. 27, III](#ref-anac400)):** Required (delay > 4h and overnight stay required). Mariana is booked into partner hotel **#HT8842**. (Note: If Mariana were a resident of São Paulo, lodging could be waived under [Art. 27, § 1º](#ref-anac400), but round-trip transfer home would remain mandatory; as an onward international traveler, hotel is required).
5. **Ground Transport ([Art. 27, III](#ref-anac400)):** Required. Round-trip transfer between GRU Airport and hotel #HT8842.
6. **Re-accommodation ([Art. 28, I](#ref-anac400)):** Required at zero cost on earliest available flight. AC1240 at 08:15 arrives at 10:15 in Santiago, leaving 225 minutes before her 14:00 connection, satisfying the SCL 90-minute MCT.
7. **Baggage Continuity:** Under WorldTracer MCP integration, the checked bag remains with ground handling for retagging to AC1240, avoiding midnight baggage claim friction.

---

## 6. Recommended Simplified Policy Rule Table for `duty_of_care.py`

This table defines the pure deterministic rule logic for implementation in issue [#5](https://github.com/duboc/acme-airlines/issues/5) (`app/backend/src/acme/policy/duty_of_care.py`).

### 6.1 Policy Contract Specification

**Inputs:**
- `origin_airport`: `str` (IATA code, e.g. `"GRU"`)
- `destination_airport`: `str` (IATA code, e.g. `"SCL"`)
- `disruption_type`: `DisruptionType` (`"cancellation"` or `"delay"`)
- `disruption_reason`: `DisruptionReason` (`"technical_maintenance"` or `"force_majeure"`)
- `wait_hours`: `float` (hours between original scheduled departure and re-accommodated departure)
- `requires_overnight`: `bool` (whether waiting period crosses midnight / requires overnight stay)
- `passenger_is_origin_resident`: `bool` (whether passenger resides in the origin airport metropolitan area)
- `passenger_has_special_needs`: `bool` (PNAE flag under Resolução ANAC 280/2013)

**Outputs (`DutyOfCareEntitlements`):**
- `communication_included`: `bool`
- `meal_vouchers`: `list[str]` (e.g. `["dinner"]`, `["dinner", "breakfast"]`, `["lunch"]`)
- `hotel_required`: `bool`
- `ground_transfer_required`: `bool`
- `reaccommodation_required`: `bool`
- `compensation_eligible`: `bool`
- `rule_reasons`: `dict[str, str]` (mapping each entitlement to its statutory citation string)

### 6.2 Decision Matrix

| Rule ID | Input Criteria | Entitlements (`True` / values) | Cited Rule Reason | Source Link | Demo Simplification |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **DOC-01** | `wait_hours >= 1.0` | `communication_included = True` | `"ANAC 400 Art. 27, I"` | [Art. 27, I](#ref-anac400) | Full fidelity. |
| **DOC-02** | `wait_hours >= 2.0` and daytime window (11:00–15:00) | `meal_vouchers = ["lunch"]` | `"ANAC 400 Art. 27, II"` | [Art. 27, II](#ref-anac400) | Simplified time-of-day slots. |
| **DOC-03** | `wait_hours >= 2.0` and evening window (18:00–23:00) | `meal_vouchers = ["dinner"]` | `"ANAC 400 Art. 27, II"` | [Art. 27, II](#ref-anac400) | Simplified time-of-day slots. |
| **DOC-04** | `wait_hours >= 4.0` and `requires_overnight` and (evening start) | `meal_vouchers = ["dinner", "breakfast"]` | `"ANAC 400 Art. 27, II"` | [Art. 27, II](#ref-anac400) | Multi-meal allocation for overnight. |
| **DOC-05** | `wait_hours >= 4.0` and `requires_overnight == True` and `passenger_is_origin_resident == False` | `hotel_required = True`, `ground_transfer_required = True` | `"ANAC 400 Art. 27, III"` | [Art. 27, III](#ref-anac400) | Standard non-resident overnight lodging. |
| **DOC-06** | `wait_hours >= 4.0` and `requires_overnight == True` and `passenger_is_origin_resident == True` | `hotel_required = False`, `ground_transfer_required = True` | `"ANAC 400 Art. 27, § 1º"` | [Art. 27, § 1º](#ref-anac400) | Resident transfer home carve-out. |
| **DOC-07** | `wait_hours >= 4.0` and `requires_overnight == False` and `passenger_has_special_needs == True` | `hotel_required = True`, `ground_transfer_required = True` | `"ANAC 400 Art. 27, § 2º"` | [Art. 27, § 2º](#ref-anac400) | Day-room hotel for PNAE without overnight. |
| **DOC-08** | `wait_hours < 4.0` (same day, no overnight) | `hotel_required = False`, `ground_transfer_required = False` | `"ANAC 400 Art. 27, III (threshold not met)"` | [Art. 27, III](#ref-anac400) | Within-2h/4h re-accommodation exempt from hotel. |
| **DOC-09** | `disruption_type == "cancellation"` or `wait_hours >= 4.0` | `reaccommodation_required = True` | `"ANAC 400 Art. 21, I & Art. 28"` | [Art. 21](#ref-anac400), [Art. 28](#ref-anac400) | First available flight re-accommodation. |
| **DOC-10** | `disruption_reason == "technical_maintenance"` and `wait_hours >= 4.0` | `compensation_eligible = True` | `"CDC Art. 14 / STJ REsp 1.584.465 (fortuito interno)"` | [CDC Art. 14](#ref-cdc), [STJ Precedent](#ref-stj-resp1584465) | Civil liability flag; demo leaves actual claim value to CRM. |
| **DOC-11** | `disruption_reason == "force_majeure"` (e.g. closed airport) | `compensation_eligible = False` | `"CDC Art. 14, § 3º, II (excludente de responsabilidade)"` | [CDC Art. 14](#ref-cdc) | Force majeure excludes damages, but care remains mandatory (Art. 26 § 3º). |

### 6.3 Demo Simplifications & Assumptions
1. **Fixed Voucher Valuation:** In reality, ANAC 400 does not set fixed monetary amounts for food vouchers (airlines typically provide R$ 50–90 per meal). For the demo, `duty_of_care.py` issues categorical entitlement tokens (`"dinner"`, `"breakfast"`), leaving dollar amounts to the Concierge Agent.
2. **Simplified Resident Flag:** The engine takes a boolean `passenger_is_origin_resident`. For Mariana Silva, CRM registers her home address as Curitiba (CWB) or international transit, so `passenger_is_origin_resident = False`.
3. **EU261 Suppressed:** Because departure is GRU and arrival is SCL on a non-EU carrier, `duty_of_care.py` bypasses EU261 €400 calculations, noting `"EU261 non-applicable: third-country route (Art. 3(1))"`.

---

## 7. Gaps & Limitations

1. **Monetary Indemnity Amounts:** Brazilian law does not prescribe fixed administrative cancellation compensation (unlike EU261 Art. 7). Judicial damages range from R$ 3,000 to R$ 10,000 depending on judicial discretion. For the MVP, `compensation_eligible` is an informational compliance flag rather than a hard currency payout.
2. **Airport Transfer Radius:** ANAC 400 does not set a maximum distance for hotel transfers. The partner hotel #HT8842 is located in the Guarulhos airport perimeter (within 10 km).
3. **PNAE Registry Ingestion:** ANAC Resolução 280/2013 requires passengers to notify assistance needs at least 48 to 72 hours prior to departure. The demo relies on the CRM loyalty profile (`special_needs` flag).

---

## 8. References & Primary Sources

### Brazilian Primary Legal Sources
- <a id="ref-anac400"></a>**ANAC Resolução Nº 400, de 13 de dezembro de 2016:** *Dispõe sobre as Condições Gerais de Transporte Aéreo Regular de Passageiros, Doméstico e Internacional.*  
  Official URL: [https://www.gov.br/anac/pt-br/assuntos/regulados/empresas-aereas/repositorio-de-respostas-as-demandas-institucionais/resolucao-no-400-de-13-12-2016](https://www.gov.br/anac/pt-br/assuntos/regulados/empresas-aereas/repositorio-de-respostas-as-demandas-institucionais/resolucao-no-400-de-13-12-2016)
- <a id="ref-anac280"></a>**ANAC Resolução Nº 280, de 11 de julho de 2013:** *Procedimentos relativos à acessibilidade de passageiros com necessidade de assistência especial (PNAE).*  
  Official URL: [https://www.anac.gov.br/assuntos/legislacao/legislacao-1/resolucoes/resolucoes-2013/resolucao-no-280-de-11-07-2013](https://www.anac.gov.br/assuntos/legislacao/legislacao-1/resolucoes/resolucoes-2013/resolucao-no-280-de-11-07-2013)
- <a id="ref-cdc"></a>**Lei Federal Nº 8.078, de 11 de setembro de 1990 (Código de Defesa do Consumidor - CDC):** Art. 14 (Responsabilidade objetiva do fornecedor de serviços).  
  Official URL: [http://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm](http://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm)
- <a id="ref-stj-resp1584465"></a>**Superior Tribunal de Justiça (STJ) - REsp 1.584.465/MG:** *Transporte aéreo. Cancelamento de voo. Problemas técnicos na aeronave. Fortuito interno que não elide a responsabilidade da transportadora.* Rel. Min. Marco Aurélio Bellizze, Terceira Turma, DJe 07/11/2016.  
  Official URL: [https://processo.stj.jus.br/processo/pesquisa/?termo=1584465](https://processo.stj.jus.br/processo/pesquisa/?termo=1584465)
- <a id="ref-stj-aresp1849567"></a>**Superior Tribunal de Justiça (STJ) - AgInt no AREsp 1.849.567/SP:** *Atraso e cancelamento de voo. Manutenção não programada de aeronave. Fortuito interno. Dever de indenizar.* Quarta Turma, DJe 20/09/2021.  
  Official URL: [https://processo.stj.jus.br/processo/pesquisa/?termo=1849567](https://processo.stj.jus.br/processo/pesquisa/?termo=1849567)

### European Union Primary Legal Sources
- <a id="ref-eu261"></a>**Regulation (EC) No 261/2004 of the European Parliament and of the Council of 11 February 2004:** *Establishing common rules on compensation and assistance to passengers in the event of denied boarding and of cancellation or long delay of flights.*  
  Official URL: [https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32004R0261](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32004R0261)
- <a id="ref-cjeu-airhelp"></a>**Court of Justice of the European Union (CJEU) - Case C-451/19 (*Airhelp Ltd v Scandinavian Airlines System*):** Judgment of 26 March 2020 on territorial scope (Art. 3(1)).  
  Official URL: [https://curia.europa.eu/juris/liste.jsf?num=C-451/19](https://curia.europa.eu/juris/liste.jsf?num=C-451/19)
- <a id="ref-cjeu-ceske"></a>**Court of Justice of the European Union (CJEU) - Case C-502/18 (*CS and Others v České aerolinie a.s.*):** Judgment of 11 July 2019 on connecting flights operated outside the EU.  
  Official URL: [https://curia.europa.eu/juris/liste.jsf?num=C-502/18](https://curia.europa.eu/juris/liste.jsf?num=C-502/18)
- <a id="ref-cjeu-wallentin"></a>**Court of Justice of the European Union (CJEU) - Case C-549/07 (*Wallentin-Hermann v Alitalia*):** Judgment of 22 December 2008 on technical defects and extraordinary circumstances (Art. 5(3)).  
  Official URL: [https://curia.europa.eu/juris/liste.jsf?num=C-549/07](https://curia.europa.eu/juris/liste.jsf?num=C-549/07)
- <a id="ref-cjeu-mcdonagh"></a>**Court of Justice of the European Union (CJEU) - Case C-12/11 (*McDonagh v Ryanair Ltd*):** Judgment of 31 January 2013 on non-excludable duty of care under Art. 9.  
  Official URL: [https://curia.europa.eu/juris/liste.jsf?num=C-12/11](https://curia.europa.eu/juris/liste.jsf?num=C-12/11)

### Chilean Primary Legal Sources
- <a id="ref-chile-codigo"></a>**Código Aeronáutico de Chile (Ley N° 18.916):** Título VIII, Capítulo V, Párrafo 1 (Artículos 133 A a 133 F), incorporado por Ley N° 20.831 (2015) y modificado por Ley N° 21.392 (2021).  
  Official URL: [https://www.bcn.cl/leychile/navegar?idNorma=30287](https://www.bcn.cl/leychile/navegar?idNorma=30287)
- <a id="ref-chile-sernac"></a>**Ley N° 19.496 sobre Protección de los Derechos de los Consumidores:** Artículos 12 y 23.  
  Official URL: [https://www.bcn.cl/leychile/navegar?idNorma=61438](https://www.bcn.cl/leychile/navegar?idNorma=61438)
- <a id="ref-jac"></a>**Junta de Aeronáutica Civil (JAC) / SERNAC:** *Guía de Derechos de los Pasajeros del Transporte Aéreo Comercial.*  
  Official URL: [https://www.jac.gob.cl](https://www.jac.gob.cl)
