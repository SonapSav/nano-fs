# Reference data

Published values used for validation. Every number in a validation test must cite an entry here.

## C172P Pilot's Operating Handbook (POH)

- **Documents:** Cessna *Pilot's Operating Handbook and FAA Approved Airplane Flight Manual*, Model 172P, two editions whose Figure 5-3 and 5-8 tables were checked to be identical:
  - 1985 Model 172P, copyright 1984, Original Issue. Civil Air Patrol copy: https://tx435.cap.gov/media/cms/C172PPOHwoSupplements_0A69C5AA130B9.pdf (has handwritten amendments in Section 2).
  - 1981 Model 172P. LSV Rhein copy (D-EKRM): https://www.lsvr.de/de/wp-content/uploads/2019/10/POH-Cessna-172-P-D-EKRM.pdf (clean Section 2; used for CG limits).
  - Both retrieved 2026-10-04, scanned with OCR text. Not stored in this repo.
  - Figure numbers below follow the 1985 edition. The 1981 edition has no Figure 5-2, so its Cruise Performance table is Figure 5-7 (same values).
- **Relevance:** JSBSim's `c172p` model. The model file itself states it is built from public data and "guesses", validated only to "fly right", so expect approximate agreement.

### Figure 5-8, Cruise Performance (excerpt)

Conditions: 2400 lb, recommended lean mixture, standard temperature. Speeds include wheel fairings (about +2 kt).

| Pressure alt (ft) | RPM | % BHP | KTAS | GPH |
|---|---|---|---|---|
| 4000 | 2200 | 54 | 96 | 6.1 |
| 4000 | 2300 | 59 | 102 | 6.6 |
| 6000 | 2200 | 52 | 95 | 5.9 |
| 6000 | 2300 | 57 | 101 | 6.4 |

### Mixture leaning and fuel density (used for the fuel-flow checks)

- **Section 4, Cruise:** "To achieve the recommended lean mixture fuel consumption figures shown in Section 5, the mixture should be leaned until engine RPM peaks and then leaned further until it drops 25-50 RPM."
- **Figure 4-4, EGT Table:** Recommended lean (Pilot's Operating Handbook and Power Computer) = 50 F rich of peak EGT; best economy = peak EGT.
- **Section 6, sample loading:** "Usable Fuel (At 6 Lbs./Gal.)". The JSBSim c172p tanks use 6.6 lb/gal (JSBSim's default fuel), so the model's own `fuel-flow-rate-gph` reads about 9% low against POH gallons; checks convert fuel mass at 6 lb/gal.

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
- **3.7.3 Low altitude (Category C: approach and landing), used by the approach task with wind:**
  - 3.7.3.2 wind shear: mean wind u = u20 ln(h/z0) / ln(20/z0), z0 = 0.15 ft for Category C (2.0 ft otherwise); u20 = wind at 20 ft.
  - 3.7.3.3: tailwinds above 10 kt and crosswinds above the 3.3.7 values at 20 ft need not be considered; vector shear (90 deg over 600 ft, moderate) not modelled.
  - 3.7.3.4 turbulence: sigma_w = 0.1 u20; scale lengths and sigma_u, sigma_v from figures 10 and 11 (plots). Their standard fits, from the MathWorks "Dryden Wind Turbulence Model (Continuous)" documentation (https://www.mathworks.com/help/aeroblks/drydenwindturbulencemodelcontinuous.html, retrieved 2026-10-05), MIL-F-8785C form, h in ft, 10-1000 ft: L_w = h, L_u = L_v = h / (0.177 + 0.000823 h)^1.2, sigma_u/sigma_w = sigma_v/sigma_w = 1 / (0.177 + 0.000823 h)^0.4; components aligned with the mean wind; linear blend into the medium/high altitude model between 1000 and 2000 ft; typical u20 15 kt light, 30 kt moderate, 45 kt severe.
- Not used yet: discrete gusts (3.7.1.3, 3.7.3.5), gust angular rates.

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

Findings: RPM within about 20 and power within about 1.5 percentage points. Fuel flow is high at 6000 ft because the POH assumes leaned mixture; fuel-flow comparisons must lean first. (The GPH column above is the model's own figure at 6.6 lb/gal; at the POH's 6 lb/gal the full-rich flows are about 10% higher still. Leaned fuel flow is now a formal check, see below and `docs/VALIDATION.md`.)

Leaned (2026-10-05, POH procedures above, re-trimmed at the POH speed): 6.19 / 6.69 / 6.11 / 6.53 GPH with the RPM method and 6.07 / 6.56 / 6.00 / 6.41 GPH with the EGT method, against 6.1 / 6.6 / 5.9 / 6.4 in Figure 5-8. In the model the two methods lean to quite different mixtures (peak RPM near mixture 0.9, peak EGT near 0.65-0.7; in a real engine they are close), but after re-trimming the fuel flows differ by only about 2%. The model agrees better with the fairing-equipped speeds than with speeds 2 kt lower. The formal checks are in `configs/validation/c172p.yaml`; results in `docs/VALIDATION.md`.

## C172P POH, landing (used by the approach task)

1985 Model 172P POH, Section 4: normal approach 65-75 KIAS flaps up, 60-70 KIAS flaps down; normal landing "Touchdown -- MAIN WHEELS FIRST"; short field approach 61 KIAS; maximum demonstrated crosswind velocity (takeoff or landing) 15 kt; flaps 0-10 deg below 110 KIAS, 10-30 deg below 85 KIAS. Section 4, Stalls: the stall warning horn sounds 5-10 kt above the stall in all configurations.

## PAPI (precision approach path indicator)

Airport Lighting Company, *PAPI Style B, Type L-880 / L-881 Instruction Manual*, Rev 2.0 (https://www.airportlightingcompany.com/wp-content/uploads/2019/07/PAPI-Manual-Style-B-Rev2.0.pdf, retrieved 2026-10-05), following FAA AC 150/5345-28 and AC 150/5340-30: L-880 (4 box) aiming relative to the glide path, from the unit nearest the runway: +30', +10', -10', -30' (standard installation); the unit nearest the runway has the largest angle; units 20-30 ft (6-9 m) apart centre to centre; inboard unit at least 50 ft (15 m) from the runway edge (30 ft / 10 m allowed for small general aviation runways); visual threshold crossing height for height group 1 (general aviation) 40 ft.

## Gusty-air approach speed

FAA, *Airplane Flying Handbook* (FAA-H-8083-3C), Chapter 9 "Approaches and Landings" (https://www.faa.gov/sites/faa.gov/files/regulations_policies/handbooks_manuals/aviation/airplane_handbook/10_afh_ch9.pdf, retrieved 2026-10-06): "Pilots often use the normal approach speed plus one-half of the wind gust factors in turbulent conditions. If the normal speed is 70 knots, and the wind gusts are 15 knots, an increase of airspeed to 77 knots is appropriate." Short field: "In gusty air, no more than one-half the gust factor is added."

## Traffic pattern (circuit)

FAA Advisory Circular AC 90-66B, *Non-Towered Airport Flight Operations* (2018-03-13; https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_90-66B.pdf, retrieved 2026-10-06):
- 9.1: standard traffic patterns use left turns.
- 11.4: recommended traffic pattern altitude 1,000 ft AGL.
- 11.5: hold pattern altitude until at least abeam the approach end on downwind; start the base turn at about 45 degrees relative bearing from the approach end.
- 11.7, 11.8: after takeoff continue straight ahead until beyond the departure end; turn crosswind beyond the departure end and within 300 ft below pattern altitude; turn downwind at pattern altitude.
- Appendix (key to traffic pattern operations): complete the turn to final at least 1/4 mile from the runway.

FAA, *Airplane Flying Handbook* (FAA-H-8083-3C), Chapter 8 "Airport Traffic Patterns" (https://www.faa.gov/sites/faa.gov/files/regulations_policies/handbooks_manuals/aviation/airplane_handbook/09_afh_ch8.pdf, retrieved 2026-10-06): the downwind leg is flown "approximately 1/2 to 1 mile out from the landing runway".

## Control surface travel (type certificate)

FAA Type Certificate Data Sheet 3A12 (Cessna 172 series; copy in the NTSB docket, https://data.ntsb.gov/Docket/Document/docBLOB?ID=40338401&FileExtension=.PDF&FileName=FAA+Type+Certificate+Data+Sheet+No.+3A12-Master.PDF, retrieved 2026-10-06), section IX, Model 172P, Control Surface Movements: elevator up 28 deg (+1/-0), down 23 deg (+1/-0); ailerons up 20 deg +/-1, down 15 deg +/-1; rudder (landplane) 16 deg +/-1 left and right; wing flaps 0-30 deg (takeoff 0-10 deg). The c172p model's travel matches (checked 2026-10-06), so its elevator-limited stall speeds are not a travel error.

## Earth model and map projection (world/geo.py, viewer/geo.js)

Wikipedia, "Universal Transverse Mercator coordinate system", section "Simplified formulae" (https://en.wikipedia.org/wiki/Universal_Transverse_Mercator_coordinate_system, retrieved 2026-10-08): WGS84 a = 6378.137 km, 1/f = 298.257223563; Krueger's (1912) series to third order in n = f/(2-f) for the transverse Mercator projection, forward (latitude/longitude to easting/northing, with scale factor k and grid convergence gamma) and inverse, "accurate to around a millimeter within 3000 km of the central meridian". Used with k0 = 1, no false easting or northing, the central meridian through the world's origin, and northing measured from the origin. Checked independently in `tests/test_geo.py`: northing along the central meridian equals the meridian arc length by numerical integration (to 1e-5 m), local scale equals 1 + x^2/(2 R^2) in both directions (conformal), and the map bearing of true north equals minus the convergence (finite differences). JSBSim integrates on the WGS84 ellipsoid (its `position/lat-geod-rad` is geodetic latitude).
