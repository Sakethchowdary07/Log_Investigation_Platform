"""Rule-based knowledge base and analyzer abstraction.

The detection engine does not hardcode any cause inference. Instead it loads a
set of rules from a YAML file (default ``app/config/knowledge_base.yaml``,
overridable via the ``LOG_KB_PATH`` environment variable) and matches error
messages against them.

The :class:`Analyzer` abstract base defines the analysis interface so an
alternative implementation (for example an LLM-backed analyzer) can be plugged
in later behind the same contract. :class:`RuleBasedAnalyzer` is the v1
implementation.
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

_DEFAULT_KB_PATH = Path(__file__).parent / "config" / "knowledge_base.yaml"


def default_kb_path() -> Path:
    """Resolve the KB path, honoring the ``LOG_KB_PATH`` env var."""
    env = os.environ.get("LOG_KB_PATH")
    if env:
        return Path(env)
    return _DEFAULT_KB_PATH


@dataclass
class Rule:
    id: str
    name: str
    any_of: list[str] = field(default_factory=list)
    all_of: list[str] = field(default_factory=list)
    possible_cause: Optional[str] = None
    related_errors: list[str] = field(default_factory=list)
    suggested_steps: list[str] = field(default_factory=list)
    weight: float = 1.0

    def __post_init__(self) -> None:
        self._any_re = [re.compile(p, re.IGNORECASE) for p in self.any_of]
        self._all_re = [re.compile(p, re.IGNORECASE) for p in self.all_of]

    def matches(self, message: str) -> bool:
        if self._any_re and not any(r.search(message) for r in self._any_re):
            return False
        if self._all_re and not all(r.search(message) for r in self._all_re):
            return False
        # A rule with no patterns matches nothing.
        return bool(self._any_re or self._all_re)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "match": {"any_of": self.any_of, "all_of": self.all_of},
            "possible_cause": self.possible_cause,
            "related_errors": self.related_errors,
            "suggested_steps": self.suggested_steps,
            "weight": self.weight,
        }


@dataclass
class RuleMatch:
    """Aggregated result of matching a set of messages against the KB."""

    rule: Rule
    score: float
    match_count: int


class KnowledgeBase:
    """A loaded set of rules with matching helpers."""

    def __init__(self, rules: list[Rule]):
        self.rules = rules

    @classmethod
    def load(cls, path: Optional[os.PathLike | str] = None) -> "KnowledgeBase":
        kb_path = Path(path) if path is not None else default_kb_path()
        with open(kb_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        raw_rules = data.get("rules", []) or []
        rules: list[Rule] = []
        for raw in raw_rules:
            match = raw.get("match", {}) or {}
            rules.append(
                Rule(
                    id=str(raw.get("id", "")),
                    name=str(raw.get("name", raw.get("id", ""))),
                    any_of=list(match.get("any_of", []) or []),
                    all_of=list(match.get("all_of", []) or []),
                    possible_cause=raw.get("possible_cause"),
                    related_errors=list(raw.get("related_errors", []) or []),
                    suggested_steps=list(raw.get("suggested_steps", []) or []),
                    weight=float(raw.get("weight", 1.0)),
                )
            )
        return cls(rules)

    def match(self, message: str) -> list[Rule]:
        """Return all rules matching a single message."""
        return [rule for rule in self.rules if rule.matches(message)]

    def aggregate(self, messages: list[str]) -> Optional[RuleMatch]:
        """Score every rule across ``messages`` and return the best fit.

        The score is ``(# of matching messages) * weight``. Ties are broken by
        weight, then by the rule's order in the file.
        """
        best: Optional[RuleMatch] = None
        for rule in self.rules:
            count = sum(1 for m in messages if rule.matches(m))
            if count == 0:
                continue
            score = count * rule.weight
            candidate = RuleMatch(rule=rule, score=score, match_count=count)
            if best is None or _is_better(candidate, best):
                best = candidate
        return best

    def as_list(self) -> list[dict]:
        return [rule.to_dict() for rule in self.rules]


def _is_better(a: RuleMatch, b: RuleMatch) -> bool:
    if a.score != b.score:
        return a.score > b.score
    return a.rule.weight > b.rule.weight


class Analyzer(ABC):
    """Interface for cause-inference strategies.

    A future LLM-backed analyzer can implement this same interface.
    """

    @abstractmethod
    def analyze(self, messages: list[str]) -> Optional[RuleMatch]:
        """Infer the best-fitting cause for a set of error messages."""


class RuleBasedAnalyzer(Analyzer):
    """v1 analyzer: pure rule matching over the knowledge base."""

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def analyze(self, messages: list[str]) -> Optional[RuleMatch]:
        return self.kb.aggregate(messages)
