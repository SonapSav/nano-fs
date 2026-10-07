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

Everything else is unchanged from JSBSim's c172p.
