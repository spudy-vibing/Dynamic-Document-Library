"""LLM judges: pairwise (EVAL-9), absolute rubric (EVAL-10) and the claim checker (EVAL-11)."""

from evals.judge.pairwise import QUESTIONS, judge_version

__all__ = ["QUESTIONS", "judge_version"]
