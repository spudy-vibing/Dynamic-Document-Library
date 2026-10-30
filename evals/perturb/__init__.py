"""Grader validation (EVAL-14, EVALS_PROPOSAL §6.4): scripted defects applied to clean outputs."""

from evals.perturb.perturbations import PERTURBATIONS
from evals.perturb.samples import build_result, load_sample

__all__ = ["PERTURBATIONS", "build_result", "load_sample"]
