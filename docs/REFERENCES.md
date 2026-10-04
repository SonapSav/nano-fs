# Reference data

Published values used for validation. Every number in a validation test must cite an entry here.

## C172P Pilot's Operating Handbook (POH)

- **Document:** Cessna *Pilot's Operating Handbook and FAA Approved Airplane Flight Manual*, 1985 Model 172P, copyright 1984, Original Issue.
- **Copy used:** Civil Air Patrol, https://tx435.cap.gov/media/cms/C172PPOHwoSupplements_0A69C5AA130B9.pdf (scanned, OCR text; retrieved 2026-10-04). Not stored in this repo.
- **Relevance:** JSBSim's `c172p` model. The model file itself states it is built from public data and "guesses", validated only to "fly right", so expect approximate agreement.

### Figure 5-8, Cruise Performance (excerpt)

Conditions: 2400 lb, recommended lean mixture, standard temperature. Speeds include wheel fairings (about +2 kt).

| Pressure alt (ft) | RPM | % BHP | KTAS | GPH |
|---|---|---|---|---|
| 4000 | 2200 | 54 | 96 | 6.1 |
| 4000 | 2300 | 59 | 102 | 6.6 |
| 6000 | 2200 | 52 | 95 | 5.9 |
| 6000 | 2300 | 57 | 101 | 6.4 |

### Figure 5-3, Stall Speeds (wings level, power off, 2400 lb)

KCAS used for validation; the POH notes KIAS values are approximate.

| Flaps | Most rearward CG (KCAS) | Most forward CG (KCAS) |
|---|---|---|
| Up | 51 | 52 |
| 10° | 48 | 49 |
| 30° | 46 | 46 |

## Spot check of JSBSim c172p against Figure 5-8 (2026-10-04)

JSBSim 1.3.1, trimmed at the POH KTAS, 2390 lb (model tank capacity is 185 lb each), full-rich mixture.

| Alt (ft) | KTAS | POH RPM / %BHP / GPH | Model RPM / %BHP / GPH |
|---|---|---|---|
| 4000 | 96 | 2200 / 54 / 6.1 | 2200 / 52.6 / 6.10 |
| 4000 | 102 | 2300 / 59 / 6.6 | 2280 / 57.5 / 6.60 |
| 6000 | 95 | 2200 / 52 / 5.9 | 2223 / 51.7 / 6.95 |
| 6000 | 101 | 2300 / 57 / 6.4 | 2293 / 55.7 / 7.43 |

Findings: RPM within about 20 and power within about 1.5 percentage points. Fuel flow is high at 6000 ft because the POH assumes leaned mixture; fuel-flow comparisons must lean first. The model agrees better with the fairing-equipped speeds than with speeds 2 kt lower. Formal tests with tolerances come in step 2.
