# Reference data

Published values used for validation. Every number in a validation test must cite an entry here.

## C172P Pilot's Operating Handbook (POH)

- **Documents:** Cessna *Pilot's Operating Handbook and FAA Approved Airplane Flight Manual*, Model 172P, two editions whose Figure 5-3 and 5-8 tables were checked to be identical:
  - 1985 Model 172P, copyright 1984, Original Issue. Civil Air Patrol copy: https://tx435.cap.gov/media/cms/C172PPOHwoSupplements_0A69C5AA130B9.pdf (has handwritten amendments in Section 2).
  - 1981 Model 172P. LSV Rhein copy (D-EKRM): https://www.lsvr.de/de/wp-content/uploads/2019/10/POH-Cessna-172-P-D-EKRM.pdf (clean Section 2; used for CG limits).
  - Both retrieved 2026-10-04, scanned with OCR text. Not stored in this repo.
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

### Section 2, Center of Gravity Limits (normal category, 1981 edition)

Forward 35.0 in aft of datum at 1950 lb or less, straight line to 39.5 in at 2400 lb; aft 47.3 in at all weights. Datum: lower portion of front face of firewall.

The JSBSim c172p structural frame matches this datum to about 3 in: POH sample loading arms (front seats 37.1 in, rear seats 72.9 in, baggage area 1 95 in) vs model point masses (36, 70, 95 in). The model's empty CG (41 in) is aft of a typical 172P, so the forward limit at 2400 lb is only reachable with about 40 lb fuel.

## AAIB Bulletin 12/2020, G-CBXJ (Cessna 172S flight trial)

- **Document:** UK Air Accidents Investigation Branch, AAIB Bulletin 12/2020, report on Cessna 172S G-CBXJ, AAIB-26272. https://assets.publishing.service.gov.uk/media/5f981ff98fa8f543f46fd21a/Cessna_172S_G-CBXJ_12-20.pdf (retrieved 2026-10-04).
- **What it contains:** a test-pilot flight trial on a sister aircraft. Test aircraft 2300 lb, CG 41.43 in. Trimmed in cruise at 2500 rpm, 110 KIAS. The phugoid was excited by releasing a 2 kgf push from a ~135 KIAS, 1000 ft/min descent, controls free.
- **Stated:** "heavily damped with a period of 35-40 seconds"; vertical speed peaked at ±2500 ft/min, altitude ±300 ft, airspeed ±25 KIAS.
- **Read from Figure 5** (10 s gridlines, ~77.5 px each at 260 dpi): airspeed extrema 79 kt (t≈25.8 s), 131 kt (43.2 s), 95 kt (61.7 s), 118 kt (77.8 s) about a ~109 kt mean; full periods 35.9 s and 34.6 s; altitude peaks 3785 and 3707 ft, 35.9 s apart. Per-cycle amplitude ratio 0.41-0.47, damping ratio about 0.13. Altitude about 3500 ft.
- **Caveats:** 172S (180 hp) not 172P (160 hp), same airframe; controls free (model test is controls fixed); large amplitude; values read from a plot (about ±1.5 s).
- **Also reported:** stick free, a small rudder input led to a spiral dive within 10-15 s, i.e. the real aircraft's spiral mode is divergent.

## MIL-F-8785C, Flying Qualities of Piloted Airplanes (5 November 1980)

- **Copy used:** everyspec.com, https://everyspec.com/MIL-SPECS/MIL-SPECS-MIL-F/MIL-F-8785C_5295/ (retrieved 2026-10-04). Military specification, not an FAA requirement, but the standard quantitative yardstick for light-aircraft dynamics. Class I = small, light airplanes.
- **Level 1, Category B (cruise) values used:** short-period damping ratio 0.30-2.00 (Table IV; 0.35-1.30 for Categories A and C); phugoid damping ratio at least 0.04 (3.2.1.2); Dutch roll damping ratio at least 0.08, zeta x wn at least 0.15 rad/s, wn at least 0.4 rad/s (Table VI); roll-mode time constant at most 1.4 s (Table VII); spiral time to double at least 20 s (Table VIII; 12 s for Categories A and C).

### Atmospheric turbulence (used by the step 6 wind model)

- **3.7.1.2 Dryden form:** Phi_u = sigma_u^2 (2 L_u / pi) / (1 + (L_u Omega)^2); Phi_v = sigma_v^2 (L_v / pi) (1 + 3 (L_v Omega)^2) / (1 + (L_v Omega)^2)^2; Phi_w likewise with L_w.
- **3.7.2 Medium/high altitude (above 2000 ft):** turbulence isotropic, sigma_u = sigma_v = sigma_w; **3.7.2.1** scale lengths L_u = L_v = L_w = 1750 ft for the Dryden form (2500 ft for von Karman).
- **3.7.2.2 / Figure 7:** RMS intensity versus altitude. Read from the plot for the flat region below about 9000 ft: light about 5 ft/s, moderate about 10 ft/s, severe about 21 ft/s (TAS). These are plot readings, not tabulated values.
- Not used yet: the low-altitude model (3.7.3, below 1000-2000 ft, Category C), wind shear (3.7.3.2), discrete gusts (3.7.1.3).

## Searched and not used

- **Roskam, Airplane Flight Dynamics Part I, pp. 480-482**, via the UIUC cessna172-v1 model file (https://m-selig.ae.illinois.edu/apasim/Aircraft-uiuc/cessna172-v1/aircraft.dat). The file itself notes the data is "actually Cessna 182", and it shares lineage with the FlightGear/JSBSim C172 model, so it is not an independent reference.
- **NASA CR-2337 (Kohlman, 1974)**: flight test data for a Cessna Cardinal (177), a different aircraft.
- **NASA CR-2605 (Roesch and Harlan, 1975)**: Cessna 172 stability derivatives computed with DATCOM (estimates, not measurements); scan too garbled to transcribe reliably.
- **No open, independent measurement of the C172 short-period mode was found.** The short period is checked only against the MIL-F-8785C limits.

## Spot check of JSBSim c172p against Figure 5-8 (2026-10-04)

JSBSim 1.3.1, trimmed at the POH KTAS, 2390 lb (model tank capacity is 185 lb each), full-rich mixture.

| Alt (ft) | KTAS | POH RPM / %BHP / GPH | Model RPM / %BHP / GPH |
|---|---|---|---|
| 4000 | 96 | 2200 / 54 / 6.1 | 2200 / 52.6 / 6.10 |
| 4000 | 102 | 2300 / 59 / 6.6 | 2280 / 57.5 / 6.60 |
| 6000 | 95 | 2200 / 52 / 5.9 | 2223 / 51.7 / 6.95 |
| 6000 | 101 | 2300 / 57 / 6.4 | 2293 / 55.7 / 7.43 |

Findings: RPM within about 20 and power within about 1.5 percentage points. Fuel flow is high at 6000 ft because the POH assumes leaned mixture; fuel-flow comparisons must lean first. The model agrees better with the fairing-equipped speeds than with speeds 2 kt lower. The formal checks are in `configs/validation/c172p.yaml`; results in `docs/VALIDATION.md`.
