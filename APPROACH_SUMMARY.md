## Approach Summary

### 1. How I Cleaned the Data

The VELARA tracker contains four monthly snapshots (January–April 2026) across 24 columns for 30 countries, plus three reference tabs. Several data quality issues required cleaning before analysis could begin.

**Country name normalisation.** Turkey appeared as "Turkey " (trailing space) in the February and March extracts, which would split it into two entities when grouping. All country names were stripped of leading/trailing whitespace during loading.

**Date format repair.** The March tracker (Tracker_2026_03) stored the Expected Launch Shipment Date column as raw Excel serial numbers (integers like 46395) instead of datetime objects. All other date columns in all months were correctly formatted. I converted these using the standard Excel epoch formula: `datetime(1899, 12, 30) + timedelta(days=serial)`. After conversion, March dates aligned with the other three months for most countries, confirming the repair was correct.

**Duplicate row removal.** Canada's VLR-8MG-4PEN appeared twice in the March tracker with Expected Launch Shipment Dates seven days apart. This was the cause of March having 111 data rows versus 110 in the other three months. I flagged this as both DUPLICATE_ROW and ROW_COUNT_INCONSISTENT. For slippage calculations, I used the April tracker (which has the correct single row) as the primary source.

**Status normalisation.** Colombia's Launch Status drifted across months: "Active" → "active " → "ACTIVE" → "ACTIVE ". Ireland's VLR-2MG-1VIAL row changed to "Invalid Entry" in April. These were flagged as STATUS_VALUE_DRIFT and STATUS_INVALID_ENTRY respectively.

**Invalid SKU identification.** Romania carried VLR-16MG-4PEN (a 16MG dose) in all four months, but the Ref_SKUs reference tab only lists 2MG, 4MG, 8MG, and 12MG doses. This row was flagged as SKU_NOT_IN_REFERENCE.

### 2. How I Worked Out Each Slippage Figure

For each country, the "claimed date" is the earliest Expected Launch Shipment Date across all its SKUs in the April tracker — the day the country says its launch begins. Slippage is measured from that date.

I evaluated three constraint types to find the actual earliest possible date:

**Manufacturing lead time.** Each factory has a stated lead time (Ashford: 16 weeks, Brenner: 24 weeks, Calderon: 26 weeks). The earliest a factory can ship is the Demand Loaded Date plus the lead time. For countries where demand was loaded late (Brazil: April 10 at Ashford, Czechia: April 6 at Calderon, Finland: April 15 at Calderon), the factory physically cannot produce and pack the product by the claimed shipment date. For countries where demand was never loaded (Colombia and Romania, both Direct supply route), I optimistically assumed demand could be loaded "today" (April 25, 2026) and added the relevant lead time.

**Plant setup timing.** If the Plant Setup Date falls after the Expected Launch Shipment Date for a Primary site, the product cannot ship on time. This affected the United States (setup October 28 vs. ship October 7 — 21 days), Spain (setup February 5 vs. ship January 6 — 30 days), and Peru (setup February 1 vs. ship December 30 — 33 days).

**Starting dose availability.** VELARA is a titration medicine — patients must start at the lowest dose (2MG) and step up. Three countries have their 2MG SKU shipping months after higher-dose SKUs: Canada (2MG ships March 9, 2027 vs. 12MG October 10, 2026 — 150 days), Poland (2MG June 30 vs. 12MG January 23 — 158 days), and Peru (2MG June 2 vs. 12MG December 30 — 154 days). Even though the dashboard shows these countries as GREEN (because some box is "ready"), patients cannot actually start treatment until the 2MG dose arrives.

The binding constraint is whichever pushes the date furthest. For example, Peru has both a plant setup delay (+33 days) and a starting dose delay (+154 days); the starting dose binds.

**Ashford capacity.** Ashford's total Year 1 forecast (5.42M units) exceeds its annual capacity (5.02M). This is flagged as PLANT_CAPACITY_EXCEEDED but not incorporated into individual slippage figures because we cannot determine the factory's scheduling priority for which countries get delayed.

### 3. What I Decided Not to Flag, and Why

**Ireland shipping before approval.** Ireland shipped product on July 29, 2024, five months before its approval on December 24, 2024. This looks like SHIPMENT_BEFORE_APPROVAL, but Ireland's Demand Strategy is MTO (make to order), meaning it stages product in a bonded warehouse before approval and releases it afterward. This is the same pattern described in the challenge brief's Ruritania example.

**Japan, Greece, Italy, Hungary, Denmark, Norway, Bulgaria, Slovakia — approval before submission.** All eight have Actual Approval Dates earlier than their Actual Submission Dates. However, all use Mutual Recognition or Reliance regulatory pathways, where approval from a reference country can legitimately precede the local filing. Only China and Estonia show this pattern with a National pathway, which is anomalous and was flagged.

**Chile and Vietnam blank readiness fields.** Both have blank Product Code At Plant, Warehouse Setup, and Demand Loaded fields. However, both use the Via Hub supply route, meaning a regional hub handles these functions centrally. Their blanks are operationally expected, not gaps.

**Chile and Vietnam shipment date movement.** Both moved +120 days between January and February, but both have documented Shipment Date Change Log entries citing approved change CR-118. Portugal and Turkey moved similar amounts with no documentation — only those two were flagged.

**Japan, Denmark, Bulgaria — setup dates after shipment.** All three have Secondary site roles. Secondary sites are backup facilities set up after launch, so later setup dates are by design.

**Six decoy codes not flagged.** CURRENCY_UNIT_MISMATCH (no monetary data), NEGATIVE_QUANTITY (no quantities), TIMEZONE_OFFSET_DRIFT (no timezone artifacts), PLACEHOLDER_DATE_VALUE (no placeholder dates found), DATE_PASSED_NO_REPLAN (verified — no expected dates have passed without actual shipment), SHIPMENT_DATE_MISSING (all past-due countries have actual shipment dates).

### 4. What I Am Least Sure About

**Starting dose assumption.** I assumed 2MG is the starting dose based on the titration pattern visible in the data (2MG is the lowest dose, and three countries have it shipping far later than higher doses — a pattern that only matters if patients start low). This assumption drives the three largest slippages (Canada, Poland, Peru). If the titration starts at a different dose, these rankings change significantly.

**Ashford capacity impact.** Ashford exceeds capacity by 8%, but I could not determine which specific countries would be delayed or by how much. The factory might prioritize Tier 1 markets (Germany, Brazil) over Tier 3, or it might process orders first-come-first-served. Without this information, I reported the finding but did not add capacity-driven slippage to individual countries.

**MTS countries shipping before approval.** Czechia and Finland are MTS (make to stock) countries with expected shipment dates 2-3 months before their target approval dates. I flagged this as SHIPMENT_BEFORE_APPROVAL because MTS countries — unlike MTO countries — do not use bonded warehouses. However, if MTS countries can also stage inventory pre-approval through other mechanisms, this finding would be incorrect.

**ROLLUP_ANY_NOT_ALL scope.** I limited this finding to Canada, Peru, and Poland (where the starting dose issue is clearest). Other countries may also be affected if the dashboard's "any SKU ready" logic masks problems at the individual SKU level (for example, Brazil's 2MG SKU failing the lead time check while other SKUs pass).
