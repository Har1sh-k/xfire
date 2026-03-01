"""Tests for prompt builder functions."""

from xfire.agents.prompts.defense_prompt import build_defense_prompt
from xfire.agents.prompts.judge_prompt import (
    build_judge_clarification_prompt,
    build_judge_final_prompt,
    build_judge_prompt,
)
from xfire.agents.prompts.prosecutor_prompt import build_prosecutor_prompt
from xfire.agents.prompts.review_prompt import build_review_prompt
from xfire.core.models import (
    IntentProfile,
    PRContext,
)


def _make_context(**kwargs) -> PRContext:
    defaults = {"repo_name": "test/repo", "pr_title": "Test PR"}
    defaults.update(kwargs)
    return PRContext(**defaults)


class TestBuildReviewPrompt:
    def test_includes_repo_name(self):
        ctx = _make_context()
        prompt = build_review_prompt(ctx, IntentProfile(), {})
        assert "test/repo" in prompt

    def test_includes_pr_number(self):
        ctx = _make_context(pr_number=42)
        prompt = build_review_prompt(ctx, IntentProfile(), {})
        assert "#42" in prompt

    def test_includes_skill_outputs(self):
        ctx = _make_context()
        skills = {"data_flow": "Found 3 dangerous sinks"}
        prompt = build_review_prompt(ctx, IntentProfile(), skills)
        assert "Found 3 dangerous sinks" in prompt


class TestBuildProsecutorPrompt:
    def test_contains_all_sections(self):
        prompt = build_prosecutor_prompt(
            "SQL Injection in login",
            "code_reading: unsanitized input",
            "Repo: test/repo",
            "Web API backend",
        )
        assert "SQL Injection in login" in prompt
        assert "unsanitized input" in prompt
        assert "test/repo" in prompt
        assert "Web API backend" in prompt


class TestBuildDefensePrompt:
    def test_contains_prosecutor_argument(self):
        prompt = build_defense_prompt(
            "Finding summary",
            "Evidence text",
            "The input is unsanitized",
            "Context",
            "Intent",
        )
        assert "The input is unsanitized" in prompt
        assert "Prosecutor" in prompt


class TestBuildJudgePrompt:
    def test_without_rebuttal(self):
        prompt = build_judge_prompt(
            "Finding",
            "Prosecution: it's real",
            "Defense: it's false",
            None,
            "Intent",
        )
        assert "Prosecution" in prompt.lower() or "prosecution" in prompt.lower()
        assert "Defense" in prompt.lower() or "defense" in prompt.lower()
        assert "Additional" not in prompt  # no rebuttal section

    def test_with_rebuttal(self):
        prompt = build_judge_prompt(
            "Finding",
            "Prosecution arg",
            "Defense arg",
            "Rebuttal arg",
            "Intent",
        )
        assert "Rebuttal arg" in prompt


class TestBuildJudgeClarificationPrompt:
    def test_contains_both_sides(self):
        prompt = build_judge_clarification_prompt(
            "Finding",
            "Prosecution arg",
            "Defense arg",
            "Intent",
        )
        assert "Prosecution arg" in prompt
        assert "Defense arg" in prompt
        assert "disagree" in prompt.lower()


class TestBuildJudgeFinalPrompt:
    def test_contains_all_rounds(self):
        prompt = build_judge_final_prompt(
            "Finding",
            "Round 1 prosecution",
            "Round 1 defense",
            "Judge questions",
            "Round 2 prosecution response",
            "Round 2 defense response",
            "Intent",
        )
        assert "Round 1 prosecution" in prompt
        assert "Round 1 defense" in prompt
        assert "Judge questions" in prompt
        assert "Round 2 prosecution response" in prompt
        assert "Round 2 defense response" in prompt
