"""Portable calibrated win-probability model.

Holds the fitted StandardScaler + LogisticRegression as plain numbers and does
inference in pure numpy, so the live recommender/server can score drafts WITHOUT
importing scikit-learn (and without sklearn-version pickle fragility). Training
(`lol_draft.train`) fits with sklearn and dumps this; inference just loads it.

    P(win) = sigmoid( intercept + Σ coef_k · (x_k - mean_k) / scale_k )

where x is the feature vector from `features.draft_features` (FEATURE_NAMES
order), and (mean, scale) are the standardizer's per-feature stats.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path

from .features import FEATURE_NAMES


@dataclass
class WinProbModel:
    feature_names: list[str]
    mean: list[float]        # StandardScaler.mean_  (raw-feature means)
    scale: list[float]       # StandardScaler.scale_ (raw-feature std devs)
    coef: list[float]        # logistic coefficients on STANDARDIZED features
    intercept: float
    meta: dict               # provenance: n, patch, cv metrics, built_at, ...

    # --- inference ---
    def logit(self, feats: dict[str, float]) -> float:
        total = self.intercept
        for k, c, m, s in zip(self.feature_names, self.coef, self.mean, self.scale):
            total += c * ((feats[k] - m) / (s if s else 1.0))
        return total

    def predict_proba(self, feats: dict[str, float]) -> float:
        return 1.0 / (1.0 + math.exp(-self.logit(feats)))

    def contributions(self, feats: dict[str, float]) -> dict[str, float]:
        """Each feature's additive push on the logit (signed). Sums (with the
        intercept) to logit(feats); useful for explaining a candidate's P(win)."""
        out: dict[str, float] = {}
        for k, c, m, s in zip(self.feature_names, self.coef, self.mean, self.scale):
            out[k] = c * ((feats[k] - m) / (s if s else 1.0))
        return out

    # --- raw (un-standardized) form, for interpretation/deployment ---
    def raw_coefficients(self) -> tuple[float, dict[str, float]]:
        """Equivalent model on RAW features:
        logit = b0 + Σ b_k · x_k. Folds the standardizer back in."""
        b0 = self.intercept
        raw: dict[str, float] = {}
        for k, c, m, s in zip(self.feature_names, self.coef, self.mean, self.scale):
            s = s if s else 1.0
            raw[k] = c / s
            b0 -= c * m / s
        return b0, raw

    # --- persistence (JSON, no pickle) ---
    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "WinProbModel":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_sklearn(cls, scaler, clf, meta: dict) -> "WinProbModel":
        return cls(
            feature_names=list(FEATURE_NAMES),
            mean=[float(x) for x in scaler.mean_],
            scale=[float(x) for x in scaler.scale_],
            coef=[float(x) for x in clf.coef_[0]],
            intercept=float(clf.intercept_[0]),
            meta=meta,
        )


def default_model_path():
    from . import config
    return config.PROJECT_DIR / "data" / "models" / "winprob.json"
