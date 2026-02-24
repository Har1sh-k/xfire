"""Default configuration values for CrossFire."""

DEFAULT_CONFIG: dict = {
    "repo": {
        "purpose": "",
        "intended_capabilities": [],
        "sensitive_paths": ["auth/", "payments/", "migrations/"],
    },
    "analysis": {
        "context_depth": "deep",
        "max_related_files": 20,
        "include_test_files": True,
    },
    "agents": {
        "claude": {
            "enabled": True,
            "mode": "cli",
            "cli_command": "claude",
            "cli_args": ["--output-format", "json"],
            "model": "claude-sonnet-4-20250514",
            "api_key_env": "ANTHROPIC_API_KEY",
            "timeout": 300,
        },
        "codex": {
            "enabled": True,
            "mode": "cli",
            "cli_command": "codex",
            "cli_args": [],
            "model": "o3-mini",
            "api_key_env": "OPENAI_API_KEY",
            "timeout": 300,
        },
        "gemini": {
            "enabled": True,
            "mode": "cli",
            "cli_command": "gemini",
            "cli_args": [],
            "model": "gemini-2.5-pro",
            "api_key_env": "GOOGLE_API_KEY",
            "timeout": 300,
        },
        "debate": {
            "role_assignment": "evidence",
            "fixed_roles": {
                "prosecutor": "claude",
                "defense": "codex",
                "judge": "gemini",
            },
            "defense_preference": ["codex", "claude", "gemini"],
            "judge_preference": ["codex", "gemini", "claude"],
            "max_rounds": 2,
            "require_evidence_citations": True,
            "min_agents_for_debate": 2,
        },
        "skills": {
            "code_navigation": True,
            "data_flow_tracing": True,
            "git_archeology": True,
            "config_analysis": True,
            "dependency_analysis": True,
            "test_coverage_check": True,
        },
    },
    "severity_gate": {
        "fail_on": "high",
        "min_confidence": 0.7,
        "require_debate": True,
    },
    "suppressions": [],
}
