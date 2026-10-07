# Third-party components

This project's own code is under the MIT licence (`LICENSE`). It bundles or derives from
the following, which keep their own licences.

| Component | Where | Licence |
|---|---|---|
| [three.js](https://threejs.org/) 0.186.1 and its Sky addon | `flightsim/viewer/vendor/` | MIT (`three.LICENSE`, notices in the files) |
| [Barlow Condensed](https://github.com/jpt/barlow) font | `flightsim/viewer/vendor/fonts/` | SIL Open Font License 1.1 (`OFL.txt`) |
| JSBSim Cessna 172P model, modified | `flightsim/aircraft/c172p_tuned/` | LGPL-2.1 or later (`LICENSE` and `README.md` there) |

Installed as dependencies (not included in this repository): [JSBSim](https://github.com/JSBSim-Team/jsbsim)
(LGPL-2.1 or later), NumPy, SciPy (BSD), PyArrow (Apache-2.0), Gymnasium (MIT),
Stable-Baselines3 (MIT), PyTorch (BSD-style), websockets (BSD-3-Clause), PyYAML (MIT).

Reference data: published figures from the Cessna Model 172P Pilot's Operating Handbook,
FAA handbooks and advisory circulars, MIL-F-8785C and an AAIB bulletin are cited with
their sources in `docs/REFERENCES.md`. They are used as test references only.
