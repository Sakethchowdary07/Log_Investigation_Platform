import textwrap

from app.knowledge_base import KnowledgeBase, RuleBasedAnalyzer


def test_db_pool_rule_matches_and_aggregates():
    kb = KnowledgeBase.load()  # honors LOG_KB_PATH from temp_kb_path fixture
    messages = [
        "DB timeout while acquiring connection from pool",
        "connection pool exhausted",
        "Connection refused by database host",
        "Upstream returned HTTP 503 Service Unavailable",
    ]
    match = kb.aggregate(messages)
    assert match is not None
    assert match.rule.possible_cause == "Database connection pool exhaustion"
    assert match.rule.related_errors == ["DB timeout", "Connection refused", "HTTP 503"]
    assert match.rule.suggested_steps == [
        "Check DB connections",
        "Check CPU",
        "Check connection pool",
    ]


def test_single_message_match():
    kb = KnowledgeBase.load()
    rules = kb.match("java.lang.OutOfMemoryError: Java heap space")
    ids = {r.id for r in rules}
    assert "out_of_memory" in ids


def test_no_match_returns_none():
    kb = KnowledgeBase.load()
    assert kb.aggregate(["a perfectly normal log line"]) is None


def test_analyzer_interface():
    kb = KnowledgeBase.load()
    analyzer = RuleBasedAnalyzer(kb)
    match = analyzer.analyze(["disk full: no space left on device"])
    assert match is not None
    assert match.rule.possible_cause == "Disk space exhaustion"


def test_custom_kb_is_user_extensible(tmp_path):
    """Loading a custom KB with a NEW rule changes matching behavior."""
    custom = tmp_path / "custom_kb.yaml"
    custom.write_text(
        textwrap.dedent(
            """
            rules:
              - id: certificate_expired
                name: TLS certificate expired
                match:
                  any_of:
                    - "certificate has expired"
                    - "certificate expired"
                possible_cause: "Expired TLS certificate"
                related_errors: ["SSL handshake failure"]
                suggested_steps: ["Renew certificate", "Reload service"]
                weight: 1.0
            """
        ),
        encoding="utf-8",
    )
    kb = KnowledgeBase.load(custom)
    # The default KB has no such rule; this proves it is not hardcoded.
    match = kb.aggregate(["TLS handshake failed: certificate has expired"])
    assert match is not None
    assert match.rule.possible_cause == "Expired TLS certificate"
    assert match.rule.suggested_steps == ["Renew certificate", "Reload service"]

    # And a message the default KB would catch is NOT caught by this custom KB.
    assert kb.aggregate(["connection pool exhausted"]) is None
