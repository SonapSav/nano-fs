# c172p_tuned

A modified copy of the Cessna 172P model (`c172p`) distributed with
[JSBSim](https://github.com/JSBSim-Team/jsbsim) 1.3.1, under the
**GNU Lesser General Public License, version 2.1 or later** (see `LICENSE` in this
folder). Unlike the rest of this repository (MIT), the files in this folder stay under
the LGPL.

Changes from JSBSim's `c172p` (2026-10-05/06, documented in `PROJECT.md` and
`docs/VALIDATION_c172p_tuned.md`):

- `c172p_tuned.xml`: the c172p definition with the engine's propeller pointing at the
  tuned propeller below, and main gear friction coefficients changed (static 0.47,
  dynamic 0.29) to match the POH landing ground roll.
- `Engines/prop_75in2f_tuned.xml`: the c172p propeller (`prop_75in2f`) with its power
  coefficient raised 15 % at advance ratios up to 0.45 (blending back to the original by
  0.65) and its thrust coefficient raised 15 % up to 0.2 (blending back by 0.45), to
  match the POH climb and takeoff figures.

- `c172p_tuned.xml` (2026-10-10): the wing's lift near the stall raised (from alpha 0.14
  rad, +10 % at the peak, back to the original by 0.36 rad where the post-stall column
  joins it) to match the POH's aft-CG stall speeds, and the elevator's pitch
  effectiveness from -1.122 to -1.28 /rad (Roskam's value, via the UIUC model, for the
  closely related Cessna 182) so the elevator reaches the POH's forward-CG stalls.
- `Engines/prop_75in2f_tuned.xml` (2026-10-10): thrust coefficient x 1.22 more at advance
  ratios up to 0.25 (blending back by 0.40): the 2026-10-06 tuning had matched the roll to
  51 KCAS, but the POH's 51 KIAS lift-off is 55.7 KCAS (Figure 5-1); the takeoff roll now
  matches (894 ft, POH 892), the climb unchanged.

- `c172p_tuned.xml` (2026-10-10): empty weight and CG of the POH's sample airplane (1467 lb
  at 39.06 in; was 1500 lb at 41.0), fuel tanks at the POH's fuel arm (47.9 in; was 56),
  seats at the sample loading's arms (front 37.1 in, rear 72.9 in; were 36 and 70).

Everything else is unchanged from JSBSim's c172p.
