from collections import defaultdict
from uuid import uuid4

from pid_framework.models import Document, Finding, Review


def deterministic_review(document: Document) -> list[Finding]:
    findings: list[Finding] = []
    grouped = defaultdict(list)
    for equipment in document.graph.equipment:
        grouped[equipment.tag.casefold()].append(equipment)
    for items in grouped.values():
        if len(items) > 1:
            findings.append(
                Finding(
                    rule_id="DATA-REPEATED-TAG",
                    entity_ids=[e.id for e in items],
                    evidence_ids=list(dict.fromkeys(r for e in items for r in e.evidence_ids)),
                    observation=f"Tag {items[0].tag} appears in multiple extracted locations.",
                    requested_action=(
                        "Verify whether these are repeated references or distinct equipment with "
                        "a conflicting tag. Repetition alone is not a technical defect."
                    ),
                    category="data_quality",
                    priority="low",
                )
            )
    holds = [e for e in document.evidence if e.id.startswith("hold-")]
    if holds:
        findings.append(
            Finding(
                rule_id="QUERY-DOCUMENTED-HOLDS",
                entity_ids=[],
                evidence_ids=[e.id for e in holds],
                observation=f"{len(holds)} HOLD text occurrences were extracted from the source.",
                requested_action=(
                    "Review each marked location and the drawing's HOLDS notes. "
                    "Confirm disposition with the responsible discipline; "
                    "occurrence count is not a count of defects."
                ),
                category="engineering_query",
            )
        )
    for equipment in document.graph.equipment:
        if not equipment.confirmed:
            continue
        values = defaultdict(list)
        for spec in equipment.specifications:
            values[spec.name.casefold()].append(spec)
        for name, specs in values.items():
            if len({s.value.casefold() for s in specs}) > 1:
                findings.append(
                    Finding(
                        rule_id="SPEC-CONFLICT",
                        entity_ids=[equipment.id],
                        evidence_ids=list(dict.fromkeys(r for s in specs for r in s.evidence_ids)),
                        observation=f"Conflicting submitted values for {name} on {equipment.tag}.",
                        requested_action=(
                            "Compare the cited source regions and resolve applicability, units, "
                            "and operating versus design conditions before changing the drawing."
                        ),
                        category="potential_technical_issue",
                    )
                )
    return findings


def validate_findings(document: Document, findings: list[Finding]) -> None:
    evidence = {e.id for e in document.evidence}
    entities = {e.id for e in document.graph.equipment}
    for finding in findings:
        if set(finding.evidence_ids) - evidence or set(finding.entity_ids) - entities:
            raise ValueError("Reviewer cited an unknown evidence or equipment ID")
        if finding.entity_ids:
            related = {
                ref
                for entity in document.graph.equipment
                if entity.id in finding.entity_ids
                for ref in (
                    entity.evidence_ids + [r for s in entity.specifications for r in s.evidence_ids]
                )
            }
            if set(finding.evidence_ids) - related:
                raise ValueError("Reviewer cited evidence unrelated to its equipment")


def make_review(document: Document, extra: list[Finding], agent_used: bool) -> Review:
    validate_findings(document, extra)
    return Review(
        id=str(uuid4()),
        document_id=document.id,
        document_version=document.version,
        revision=document.revision,
        findings=deterministic_review(document) + extra,
        agent_used=agent_used,
        limitations=document.limitations
        + [
            "No findings does not establish drawing correctness or compliance.",
            "Technical coverage is limited to submitted specification conflicts.",
            "HOLDs and repeated tags are review queries, not proven engineering defects.",
            "No hydraulic, relief sizing, SIL, or process safety calculation is performed.",
        ],
    )
