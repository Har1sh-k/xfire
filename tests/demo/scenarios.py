# Re-export from the installed package so tests can import from either location.
from crossfire.demo.scenarios import (  # noqa: F401
    SCENARIO_LABELS,
    SCENARIOS,
    scenario_both_accept,
    scenario_defender_wins,
    scenario_judge_questions,
)
