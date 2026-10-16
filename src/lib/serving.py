"""Validated MLflow pyfunc wrapper for the RUL model (audit finding D-02).

The registered model is a raw scikit-learn HistGradientBoostingRegressor
(``mlflow.sklearn.log_model`` in ``02_train.py``). HistGradientBoosting accepts
NaN natively, so a model-serving endpoint calling it directly returns a number
even for an all-null row instead of an error (verified on the live endpoint,
see audit/02_MODEL_VALIDATION.md). The batch job (``03_score.py``) already
guards this with ``cmapss.validate_feature_frame`` before calling the model,
but that guard only protects that one caller. Anything hitting the serving
endpoint directly (the REST API, a notebook, another service) bypasses it.

This wrapper moves the same kind of validation into the served model itself,
so every caller gets it, not just the batch job. It is registered as a new,
separate model version and never replaces ``champion`` automatically.

Validation scope, and what is intentionally NOT validated:
- Schema: exact expected feature set, no missing, no duplicate, no
  unexpected columns; the model is scored in a fixed column order.
- Values: every feature must be numeric and finite (no null/NaN/Inf).
- The one physically-grounded bound enforced is ``cycle >= 1`` (a cycle
  count cannot be zero or negative).
- No per-sensor physical range is enforced. C-MAPSS (Saxena et al., 2008)
  is a simulated dataset: Appendix A documents raw sensor units but not
  operational safety bounds, and the engineered features here (rolling
  means/stds/slopes) do not share the raw sensors' units. Inventing bounds
  for them would be exactly the kind of unfounded claim this project
  avoids elsewhere (see RELEASE_HANDOFF "Honest claim boundaries"). This
  gap is intentional and documented, not an oversight.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import mlflow.pyfunc
import pandas as pd

from .cmapss import validate_feature_frame

FEATURE_COLUMNS_FILE = "feature_columns.json"


class ValidatedRULModel(mlflow.pyfunc.PythonModel):
    """Wraps a scikit-learn RUL regressor; rejects malformed input before scoring."""

    def __init__(self, feature_columns: list[str] | None = None):
        # Set directly when wrapping a model already in memory (e.g. in tests
        # or right after mlflow.sklearn.log_model); re-read from the logged
        # artifact in load_context when MLflow reconstructs the model from disk.
        self._feature_columns = list(feature_columns) if feature_columns else None
        self.model: Any = None

    def load_context(self, context):
        import mlflow.sklearn

        self.model = mlflow.sklearn.load_model(context.artifacts["inner_model"])
        if self._feature_columns is None:
            with open(context.artifacts["feature_columns"]) as f:
                self._feature_columns = json.load(f)

    def predict(self, context, model_input, params=None):
        X = _coerce_to_dataframe(model_input)
        self._validate_schema(X)
        validate_feature_frame(X, self._feature_columns)
        return self.model.predict(X[self._feature_columns])

    def _validate_schema(self, X: pd.DataFrame) -> None:
        if self._feature_columns is None:
            raise RuntimeError("feature_columns not set; call load_context() or pass them in __init__")
        expected = self._feature_columns
        if len(set(X.columns)) != len(X.columns):
            dupes = sorted({c for c in X.columns if list(X.columns).count(c) > 1})
            raise ValueError(f"duplicate feature columns: {dupes}")
        missing = [c for c in expected if c not in X.columns]
        if missing:
            raise ValueError(f"missing feature columns: {missing[:5]}")
        extra = [c for c in X.columns if c not in expected]
        if extra:
            raise ValueError(f"unexpected feature columns: {extra[:5]}")
        for c in expected:
            if not pd.api.types.is_numeric_dtype(X[c]):
                try:
                    X[c] = pd.to_numeric(X[c])
                except (ValueError, TypeError) as e:
                    raise ValueError(f"column {c!r} is not numeric: {e}") from e


def _coerce_to_dataframe(model_input: Any) -> pd.DataFrame:
    if isinstance(model_input, pd.DataFrame):
        return model_input.copy()
    try:
        return pd.DataFrame(model_input)
    except Exception as e:
        raise ValueError(f"model_input could not be read as a table: {e}") from e


def log_validated_model(
    sk_model: Any,
    feature_columns: list[str],
    *,
    input_example: pd.DataFrame,
    registered_model_name: str | None = None,
    artifact_name: str = "validated_model",
):
    """Logs ``sk_model`` wrapped in ``ValidatedRULModel`` as a new MLflow model version.

    Logs the inner sklearn model as a nested artifact and the feature list as a
    JSON artifact, then logs the pyfunc wrapper referencing both, so the
    wrapper is self-contained when later loaded with ``mlflow.pyfunc.load_model``.
    Never sets or moves the ``champion`` alias: that stays a separate, explicit
    decision after the candidate has been verified by real inference.
    """
    import tempfile

    import mlflow.sklearn
    from mlflow.models import infer_signature

    with tempfile.TemporaryDirectory() as tmp:
        inner_path = Path(tmp) / "inner_model"
        mlflow.sklearn.save_model(sk_model, str(inner_path))
        feat_path = Path(tmp) / FEATURE_COLUMNS_FILE
        feat_path.write_text(json.dumps(feature_columns))

        wrapper = ValidatedRULModel(feature_columns=feature_columns)
        wrapper.model = sk_model  # used only to compute the signature locally; not serialized
        example_preds = wrapper.model.predict(input_example[feature_columns])
        signature = infer_signature(input_example, example_preds)

        return mlflow.pyfunc.log_model(
            name=artifact_name,
            python_model=ValidatedRULModel(),
            artifacts={"inner_model": str(inner_path), "feature_columns": str(feat_path)},
            signature=signature,
            input_example=input_example,
            registered_model_name=registered_model_name,
        )
