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


def models_dir() -> Path:
    from . import config
    return config.PROJECT_DIR / "data" / "models"


def default_model_path():
    return models_dir() / "winprob.json"


def default_ensemble_path():
    return models_dir() / "winprob_bootstrap.json"


# --- multi-dataset model registry --------------------------------------------
# The recommender can swap between win-prob models trained on different game sets
# (the all-patch backbone vs a single current patch). Each dataset has an id and
# a (point model, bootstrap ensemble) file pair, by convention:
#   "all"   -> winprob.json            + winprob_bootstrap.json   (the backbone)
#   "<P>"   -> winprob_patch_<P>.json  + winprob_patch_<P>_bootstrap.json
# where <P> is a major.minor patch like "16.12". The "all" id is special-cased to
# the legacy filenames so existing deploys keep working untouched.
DEFAULT_DATASET = "all"
_PATCH_PREFIX = "winprob_patch_"


def patch_model_path(patch: str) -> Path:
    return models_dir() / f"{_PATCH_PREFIX}{patch}.json"


def patch_ensemble_path(patch: str) -> Path:
    return models_dir() / f"{_PATCH_PREFIX}{patch}_bootstrap.json"


def model_paths(dataset: str) -> tuple[Path, Path]:
    """(point-model path, ensemble path) for a dataset id."""
    if dataset == DEFAULT_DATASET:
        return default_model_path(), default_ensemble_path()
    return patch_model_path(dataset), patch_ensemble_path(dataset)


def discover_datasets() -> dict[str, dict]:
    """Map dataset id -> {model_path, ensemble_path, meta} for every win-prob
    model present on disk. Always includes "all" if winprob.json exists; adds one
    entry per winprob_patch_<P>.json. `meta` is the model's provenance block
    (n_matches, patch, cv metrics, built_at) for display in the UI toggle."""
    d = models_dir()
    found: dict[str, dict] = {}
    if default_model_path().exists():
        found[DEFAULT_DATASET] = {
            "model_path": default_model_path(),
            "ensemble_path": default_ensemble_path(),
        }
    if d.is_dir():
        for p in sorted(d.glob(f"{_PATCH_PREFIX}*.json")):
            if p.name.endswith("_bootstrap.json"):
                continue
            patch = p.stem[len(_PATCH_PREFIX):]
            found[patch] = {
                "model_path": p,
                "ensemble_path": patch_ensemble_path(patch),
            }
    for ds in found.values():
        try:
            ds["meta"] = WinProbModel.load(ds["model_path"]).meta
        except (FileNotFoundError, ValueError):
            ds["meta"] = {}
    return found


# --- bootstrap ensemble -------------------------------------------------------

def _percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in [0,100]) on an ALREADY-sorted list.
    Matches numpy.percentile's default 'linear' method so offline (numpy) and
    serve-path (pure-python) summaries agree to floating point."""
    n = len(sorted_vals)
    if n == 0:
        raise ValueError("percentile of empty sequence")
    if n == 1:
        return sorted_vals[0]
    rank = (q / 100.0) * (n - 1)
    lo = int(math.floor(rank))
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


@dataclass
class WinProbEnsemble:
    """An ensemble of bootstrap-refit WinProbModels.

    Each member is a full model (scaler stats folded in) fit on one game-level
    bootstrap resample of the training data. Running all members on the same
    draft yields a DISTRIBUTION of win probabilities, which is what lets us put
    honest error bars on a point estimate and ask whether two candidates are
    actually distinguishable. Pure-python inference (no numpy/sklearn) so the
    serve path can load it without pulling heavy deps."""
    members: list[WinProbModel]
    meta: dict  # seed, n_iter, wall-clock, base-model provenance

    def __len__(self) -> int:
        return len(self.members)

    # --- distribution over a single draft ---
    def predict_distribution(self, feats: dict[str, float]) -> list[float]:
        """P(win) from every member model (length == n members)."""
        return [m.predict_proba(feats) for m in self.members]

    def _raw_arrays(self):
        """Per-member RAW coefficients, cached: (names, B0[n], {name: coef[n]}).
        Folding each member's scaler in once lets `distribution` score a draft with
        plain `b0 + Σ coef·x` instead of re-standardizing every feature on every
        one of N·candidates calls — the hot path for the all-roles board."""
        cached = getattr(self, "_raw_cache", None)
        if cached is not None:
            return cached
        names = list(self.members[0].feature_names)
        b0s: list[float] = []
        raw: dict[str, list[float]] = {k: [] for k in names}
        for m in self.members:
            b0, r = m.raw_coefficients()
            b0s.append(b0)
            for k in names:
                raw[k].append(r[k])
        cached = (names, b0s, raw)
        self._raw_cache = cached
        return cached

    def distribution(self, series: dict[str, "float | list[float]"]) -> list[float]:
        """P(win) from every member, allowing a feature to vary PER MEMBER.

        Each value in `series` is either a scalar (same for all members — a fixed
        feature value) or a length-N list (a per-member draw, e.g. a champ_strength
        resampled from its binomial standard error). Member b uses value[b] for list
        features, so feature-value uncertainty propagates through the SAME member's
        coefficients — keeping coefficient noise and feature noise paired."""
        names, b0s, raw = self._raw_arrays()
        n = len(self.members)
        logits = list(b0s)                       # start each member at its intercept
        per_member: list[tuple[list[float], list[float]]] = []
        for k in names:
            v = series.get(k, 0.0)
            coef = raw[k]
            if isinstance(v, list):
                per_member.append((coef, v))     # champ_strength: x varies by member
            else:
                for b in range(n):               # fixed feature: fold in once
                    logits[b] += coef[b] * v
        for coef, v in per_member:
            for b in range(n):
                logits[b] += coef[b] * v[b]
        return [1.0 / (1.0 + math.exp(-x)) for x in logits]

    def summarize(self, feats: dict[str, float],
                  lo: float = 5.0, hi: float = 95.0) -> dict:
        """Median / interval / sd of the win-prob distribution for one draft."""
        d = self.predict_distribution(feats)
        s = sorted(d)
        mean = sum(d) / len(d)
        var = sum((p - mean) ** 2 for p in d) / len(d)  # population sd (ddof=0)
        return {
            "median": _percentile(s, 50.0),
            "p_lo": _percentile(s, lo),
            "p_hi": _percentile(s, hi),
            "lo_pct": lo, "hi_pct": hi,
            "std": math.sqrt(var),
            "mean": mean,
            "n": len(d),
        }

    # --- distinguishability of two drafts (PAIRED across resamples) ---
    def prob_a_better(self, feats_a: dict[str, float],
                      feats_b: dict[str, float]) -> float:
        """Fraction of bootstrap resamples in which pick A's P(win) exceeds
        pick B's. PAIRED: member i scores BOTH drafts, so the shared sampling
        noise cancels and we measure the difference's sign, not two noisy
        marginals. 0.5 == coin flip (indistinguishable); ties split half/half."""
        wins = 0.0
        for m in self.members:
            pa, pb = m.predict_proba(feats_a), m.predict_proba(feats_b)
            if pa > pb:
                wins += 1.0
            elif pa == pb:
                wins += 0.5
        return wins / len(self.members)

    # --- persistence (compact JSON; feature_names stored once) ---
    def to_dict(self) -> dict:
        return {
            "feature_names": list(FEATURE_NAMES),
            "n_members": len(self.members),
            "members": [
                {"mean": m.mean, "scale": m.scale,
                 "coef": m.coef, "intercept": m.intercept}
                for m in self.members
            ],
            "meta": self.meta,
        }

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict()), encoding="utf-8")
        return p

    @classmethod
    def from_dict(cls, d: dict) -> "WinProbEnsemble":
        names = d["feature_names"]
        members = [
            WinProbModel(feature_names=list(names), mean=mm["mean"],
                         scale=mm["scale"], coef=mm["coef"],
                         intercept=mm["intercept"], meta={})
            for mm in d["members"]
        ]
        return cls(members=members, meta=d.get("meta", {}))

    @classmethod
    def load(cls, path: str | Path) -> "WinProbEnsemble":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
