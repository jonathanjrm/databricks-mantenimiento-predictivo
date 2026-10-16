"""Independent evaluation on engines held out of ALL training, tuning and CV (Option A,
audit/02_MODEL_VALIDATION.md section 5).

This answers the mission brief's Phase 3 request for a genuinely independent check: 20 of the
100 FD001 train engines are set aside with a fixed seed BEFORE anything else touches the data
(no hyperparameter tuning, no GroupKFold selection, no model fitting ever sees them). The
candidate is trained on the remaining 80 engines with the SAME FROZEN hyperparameters already
used for the registered champion (lib.modeling.HGB_PARAMS) -- nothing is re-tuned here, which
would make this a selection run rather than an independent check. The held-out engines are then
evaluated the same way the official FD001 test set is: truncate each engine's series at a
randomly chosen cycle (same deterministic method as validation_rmse_by_engine) and score RUL at
that single last observed cycle.

This is NOT a replacement for the registered champion and does not touch MLflow or Unity
Catalog: it is a local, offline analysis that produces one evidence file. Run:

    python scripts/independent_holdout_eval.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np

from lib import cmapss as C
from lib.modeling import HGB_PARAMS, make_models

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "cmapss")
HOLDOUT_SEED = 2026
N_HOLDOUT_ENGINES = 20
MAX_CUT_RUL = 145  # same convention as validation_rmse_by_engine


def main() -> dict:
    raw = C.read_cmapss(os.path.join(DATA, "train_FD001.txt"))
    all_units = sorted(raw["unit"].unique())
    assert len(all_units) == 100, f"expected 100 FD001 train engines, found {len(all_units)}"

    rng = np.random.default_rng(HOLDOUT_SEED)
    holdout_units = set(rng.choice(all_units, size=N_HOLDOUT_ENGINES, replace=False).tolist())
    fit_units = [u for u in all_units if u not in holdout_units]
    assert len(holdout_units) == N_HOLDOUT_ENGINES
    assert set(holdout_units).isdisjoint(fit_units)

    feats = C.feature_columns()

    # Fit on the 80 fit-only engines, with the SAME frozen hyperparameters as the champion.
    # No tuning, no CV selection happens here: HGB_PARAMS is imported unchanged.
    fit_raw = raw[raw["unit"].isin(fit_units)]
    fit_frame = C.rolling_features(C.add_rul_train(fit_raw, C.RUL_CLIP))
    X_fit = fit_frame[feats].astype("float64")
    y_fit = fit_frame["rul"].astype("float64")
    model = make_models(HGB_PARAMS)[list(make_models(HGB_PARAMS).keys())[-1]]
    model.fit(X_fit, y_fit)

    # Evaluate on the 20 untouched engines: truncate like the official test evaluation.
    holdout_raw = raw[raw["unit"].isin(holdout_units)]
    holdout_frame = C.rolling_features(holdout_raw)  # no RUL label: it is the evaluation target
    max_cycle = holdout_raw.groupby("unit")["cycle"].max()
    y_true, y_pred, per_engine = [], [], []
    for u in sorted(holdout_units):
        mx = int(max_cycle[u])
        cut = int(np.random.default_rng(HOLDOUT_SEED + int(u)).integers(0, min(mx - 1, MAX_CUT_RUL) + 1))
        row = holdout_frame[(holdout_frame["unit"] == u) & (holdout_frame["cycle"] == mx - cut)]
        assert len(row) == 1, f"engine {u}: expected exactly one row at the cut cycle"
        pred = float(np.clip(model.predict(row[feats].astype("float64")), 0.0, None)[0])
        y_true.append(float(cut))
        y_pred.append(pred)
        per_engine.append({"unit": int(u), "cut_cycle": mx - cut, "rul_true": float(cut), "rul_pred": pred})

    errors = np.array(y_pred) - np.array(y_true)
    result = {
        "run_at_utc": datetime.now(UTC).isoformat(),
        "methodology": (
            "20 of 100 FD001 train engines set aside with a fixed seed before any fitting, "
            "tuning or GroupKFold selection touched the data. Candidate trained on the "
            "remaining 80 engines with the frozen champion hyperparameters (no re-tuning). "
            "Evaluated with the same truncate-at-random-cycle convention as the official test "
            "set and as validation_rmse_by_engine. This is a single run, not repeated or "
            "cherry-picked."
        ),
        "n_fit_engines": len(fit_units),
        "n_holdout_engines": len(holdout_units),
        "holdout_units": sorted(int(u) for u in holdout_units),
        "hgb_params": HGB_PARAMS,
        "rmse": C.rmse(y_true, y_pred),
        "mae": float(np.mean(np.abs(errors))),
        "phm08": C.phm08_score(y_true, y_pred),
        "bias_mean_error": float(np.mean(errors)),
        "per_engine": per_engine,
        "context_for_comparison": {
            "fd001_test_rmse_champion_seen_test_set": 18.441849882005343,
            "fd001_validation_rmse_truncated_train_only": 19.724,
            "note": (
                "This holdout RMSE is expected to differ from both of the above: it uses only "
                "80 training engines (20 fewer than the champion), a different random engine "
                "split, and a different truncation seed. It is not comparable cycle-for-cycle; "
                "it is a sanity check that the model generalises to engines it never saw in any "
                "form, within the same FD001 operating regime and fault mode."
            ),
        },
    }
    return result


if __name__ == "__main__":
    result = main()
    out_path = os.path.join(
        os.path.dirname(__file__), "..", "audit", "evidence", "independent_holdout_eval.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    r = result
    print(f"RMSE={r['rmse']:.3f} MAE={r['mae']:.3f} PHM08={r['phm08']:.1f} bias={r['bias_mean_error']:.3f}")
    print(f"Written: {out_path}")
