"""Spike detection + cause inference engine.

ERROR/CRITICAL/FATAL entries are bucketed into fixed time windows. A window is
flagged as an ERROR SPIKE when its error count meets a threshold. For each
spike the knowledge base is run over the window's messages to infer a possible
cause, related errors, and ordered suggested steps.

If the entries carry no usable timestamps we fall back to a single count-based
window so detection still works.

Alongside spike detection, a data-driven SINGLE-CRITICAL-MESSAGE mode runs when
the knowledge base defines rules flagged ``critical: true``. A single matching
line (for example one ``SQLCODE -911`` or one ``S0C7``) is enough to emit a
``CRITICAL MESSAGE`` detection even when the error count is below the spike
threshold. The ``critical`` flag only changes the TRIGGER; the cause,
related errors, and suggested steps still come from the matched YAML rule.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from .knowledge_base import Analyzer, KnowledgeBase, RuleBasedAnalyzer
from .models import AnalysisResult, Detection

ERROR_LEVELS = {"ERROR", "CRITICAL", "FATAL"}

DEFAULT_WINDOW_SECONDS = 60
DEFAULT_THRESHOLD = 5


def _is_error(level: str) -> bool:
    return level.upper() in ERROR_LEVELS


class SpikeDetector:
    def __init__(
        self,
        analyzer: Optional[Analyzer] = None,
        kb: Optional[KnowledgeBase] = None,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        threshold: int = DEFAULT_THRESHOLD,
    ):
        if analyzer is None:
            if kb is None:
                kb = KnowledgeBase.load()
            analyzer = RuleBasedAnalyzer(kb)
        self.analyzer = analyzer
        # The single-critical mode needs the KB to scan for critical rules.
        # Prefer an explicit kb; otherwise reuse the one the analyzer wraps.
        if kb is None:
            kb = getattr(analyzer, "kb", None)
        self.kb = kb
        self.window_seconds = window_seconds
        self.threshold = threshold

    def _spike_windows(self, errors_with_ts):
        """Find spike windows using a sliding window over error timestamps.

        Fixed wall-clock buckets can split a single burst across a boundary so
        that neither half reaches the threshold and the spike is missed. A
        sliding window instead considers every ``window_seconds`` interval that
        begins at an actual error: any such interval containing at least
        ``threshold`` errors is a candidate spike. Overlapping/adjacent
        candidate intervals are then merged into a single detection so one
        burst is reported once (with all its errors), regardless of where it
        falls relative to a minute boundary.

        Yields ``(window_start, window_end, bucket)`` tuples in chronological
        order, where ``bucket`` is the list of error entries in the window.
        """
        ordered = sorted(errors_with_ts, key=lambda e: e.timestamp)
        span = timedelta(seconds=self.window_seconds)

        # Collect candidate intervals: each starts at an error timestamp and
        # spans window_seconds; keep those meeting the threshold.
        candidates: list[tuple[datetime, datetime]] = []
        n = len(ordered)
        for i in range(n):
            start = ordered[i].timestamp
            end = start + span
            count = 0
            for j in range(i, n):
                if ordered[j].timestamp < end:
                    count += 1
                else:
                    break
            if count >= self.threshold:
                candidates.append((start, end))

        if not candidates:
            return

        # Merge overlapping/touching candidate intervals into spikes.
        merged: list[tuple[datetime, datetime]] = []
        cur_start, cur_end = candidates[0]
        for start, end in candidates[1:]:
            if start <= cur_end:
                cur_end = max(cur_end, end)
            else:
                merged.append((cur_start, cur_end))
                cur_start, cur_end = start, end
        merged.append((cur_start, cur_end))

        for start, end in merged:
            bucket = [e for e in ordered if start <= e.timestamp < end]
            yield start, end, bucket

    def detect(self, entries, filename: str = "") -> AnalysisResult:
        total = len(entries)
        errors = [e for e in entries if _is_error(e.level)]
        error_count = len(errors)

        result = AnalysisResult(
            filename=filename,
            total_entries=total,
            error_count=error_count,
        )

        errors_with_ts = [e for e in errors if e.timestamp is not None]

        if errors_with_ts:
            for window_start, window_end, bucket in self._spike_windows(errors_with_ts):
                detection = self._build_detection(
                    bucket,
                    window_start=window_start,
                    window_end=window_end,
                )
                result.detections.append(detection)
        else:
            # No timestamps available: fall back to a single count-based window.
            if error_count >= self.threshold:
                detection = self._build_detection(errors, window_start=None, window_end=None)
                result.detections.append(detection)

        # Data-driven single-critical pass: one matching critical-rule line is
        # enough to raise a detection even below the spike threshold.
        result.detections.extend(
            self._critical_detections(entries, result.detections)
        )

        return result

    def _critical_detections(self, entries, spike_detections) -> list[Detection]:
        """Emit CRITICAL MESSAGE detections for lone critical-rule matches.

        Scans ALL entries (a lone critical message may be the only line, and
        its level may not be ERROR, e.g. an ICH408I RACF line). For each
        critical rule that matches at least one line, a single detection is
        emitted. A rule already attributed to a spike detection is skipped so a
        burst that spike detection already reported is not double-counted.
        """
        detections: list[Detection] = []
        if self.kb is None:
            return detections
        critical_rules = self.kb.critical_rules()
        if not critical_rules:
            return detections

        # Rule ids a spike detection already reported (same rule+window).
        spike_rule_ids = {
            d.matched_signature for d in spike_detections if d.matched_signature
        }

        for rule in critical_rules:
            if rule.id in spike_rule_ids:
                continue
            matching = [e for e in entries if rule.matches(e.message)]
            if not matching:
                continue

            timed = [e for e in matching if e.timestamp is not None]
            peak_time = min((e.timestamp for e in timed), default=None)
            detection = Detection(
                type="CRITICAL MESSAGE",
                window_start=None,
                window_end=None,
                peak_time=peak_time,
                error_count=len(matching),
                possible_cause=rule.possible_cause,
                related_errors=list(rule.related_errors),
                suggested_steps=list(rule.suggested_steps),
                matched_signature=rule.id,
                confidence=1.0,
            )
            detections.append(detection)
        return detections

    def _build_detection(self, bucket, window_start, window_end) -> Detection:
        messages = [e.message for e in bucket]
        peak_time = _peak_time(bucket)

        detection = Detection(
            type="ERROR SPIKE",
            window_start=window_start,
            window_end=window_end,
            peak_time=peak_time,
            error_count=len(bucket),
        )

        match = self.analyzer.analyze(messages)
        if match is not None:
            rule = match.rule
            detection.possible_cause = rule.possible_cause
            detection.related_errors = list(rule.related_errors)
            detection.suggested_steps = list(rule.suggested_steps)
            detection.matched_signature = rule.id
            # Confidence: share of window messages that matched, capped at 1.0.
            detection.confidence = round(min(1.0, match.match_count / max(1, len(bucket))), 3)
        return detection


def _peak_time(bucket) -> Optional[datetime]:
    """Pick the timestamp with the most errors (the busiest second)."""
    timed = [e for e in bucket if e.timestamp is not None]
    if not timed:
        return None
    counts: dict[datetime, int] = defaultdict(int)
    for e in timed:
        counts[e.timestamp] += 1
    # Highest count wins; on ties prefer the earliest second (the spike's
    # onset) rather than its tail, which is the more useful "peak" to show an
    # operator. Negating the epoch makes the earliest timestamp sort largest.
    return max(counts, key=lambda t: (counts[t], -t.timestamp()))


def analyze_text(
    entries,
    filename: str = "",
    kb: Optional[KnowledgeBase] = None,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    threshold: int = DEFAULT_THRESHOLD,
) -> AnalysisResult:
    """Convenience wrapper: build a detector and run it over entries."""
    detector = SpikeDetector(
        kb=kb,
        window_seconds=window_seconds,
        threshold=threshold,
    )
    return detector.detect(entries, filename=filename)
