# Custom engineering drawing framework: design and implementation specification

## 1. Scope and responsibility

Build a traceable platform for reviewing engineering drawings, initially pump-related
P&IDs, and authoring equipment-focused detailed drawing packages.

Two operating modes share one evidence-linked engineering model:

1. **Review:** identify potential issues, distinguish uncertainty from defects, and produce
   actionable comments with source revision and precise locations.
2. **Equipment detailing:** select depicted equipment, resolve package boundaries,
   gather specifications, produce an editable draft, and obtain discipline approval.

Neither a vision model nor agreement between agents establishes engineering correctness.
The owner must define project-specific acceptance criteria and applicable design requirements.

## 2. Logical architecture

```text
Browser drawing workspace / engineering authoring-tool plugin
                        |
API + identity + project permissions + approval policy
                        |
Durable review/detailing workflow + immutable state snapshots
                        |
   +--------------------+-------------------------+
   |                    |                         |
Ingestion          Review services          Detailing services
   |                    |                         |
CAD/PDF parsing    Applicability resolver    Equipment/package selector
OCR/vision         Deterministic rules      Cross-sheet context resolver
Symbol detection   Specialist agents        Specification reconciliation
Line tracing       Evidence verifier        Proposed-change planner
Graph validation   Finding consolidation    Deterministic CAD renderer
   |                    |                         |
   +--------------------+-------------------------+
                        |
Engineering graph + evidence index + document/revision registry
                        |
Original documents / standards / datasheets / audit event ledger
```

Keep the document interpretation graph distinct from the workflow execution graph.
The former describes plant facts; the latter describes processing and approval state.

## 3. Baseline choice

Use **Microsoft Agent Framework** for bounded model/tool interactions on Azure.
The prototype implements the real SDK's `Agent` and schema-constrained responses.
It deliberately uses ordinary Python orchestration for the small initial workflow.
It does not claim distributed durability or production workflow checkpointing.

For production, implement Microsoft Agent Framework workflows with explicit typed stages,
checkpoint storage, bounded retries, cancellation and human approval. Pin SDK versions
and test migration behavior before upgrading. Keep extraction, graph, rules, and rendering
behind ordinary service interfaces so a later LangGraph adapter does not change semantics.
Do not introduce a second orchestrator merely to coordinate the first.

## 4. Canonical engineering data model

Production entities:

- Project, document, revision, page, coordinate transformation and source checksum.
- Equipment and equipment ports/nozzles.
- Pipe segments, fittings, valves, instruments and signal interfaces.
- Connections with endpoints, connection type, direction when known and source evidence.
- Off-page connectors and references to other drawings.
- Instrument loops and control/protective function references.
- Specifications with units, design/operating context, provenance and source precedence.
- Requirements with edition, clause, applicability and licensed source reference.
- Findings, review decisions, proposed changes and formal approval events.

Each claim requires a source location or a reproducible calculation with cited inputs.
Track provenance states separately: observed, inferred, engineer-confirmed, proposed,
approved, rejected and superseded. Do not equate engineer confirmation of extraction
with formal approval of the design.

The prototype stores a smaller equipment/connection graph as validated JSON in SQLite.
This intentionally omits ports, separate signal topology, semantic unit reconciliation,
and automated document association. Production graph traversal must operate at port level.

Coordinate convention: one-based page number and normalized top-left bounding box
`[left, top, right, bottom]`. Retain transforms for source CAD, PDF rotation/crop,
rasterization and tiled inference before supporting those input variations.

## 5. Drawing interpretation

Prefer intelligent CAD exports where available. For PDFs:

1. Classify page type and establish readable text/vector/image layers.
2. Extract text and geometry while preserving stable source coordinates.
3. Retrieve the applicable project symbol legend.
4. Detect symbols and their ports at suitable image resolution.
5. Trace process and instrument lines; distinguish intersections from junctions.
6. Associate labels, notes, line numbers and specifications with entities.
7. Resolve cross-sheet connectors using the drawing register and revisions.
8. Validate graph consistency and send uncertain regions to engineer correction.

Use tile overlap and page-level context to avoid truncating long lines or missing
small annotations. Propagate confidence per claim and connection, not just per document.
Do not report a missing connection based solely on a low-confidence extraction.

Image-only drawings need OCR and a separate visual extraction evaluation set.
General document OCR is not a substitute for symbol and topology recognition.

## 6. Review workflow and agents

```text
Register immutable inputs
 -> extraction and graph validation
 -> extraction correction checkpoint
 -> determine applicable requirements
 -> parallel deterministic and bounded specialist checks
 -> verify evidence and requirement applicability
 -> deduplicate without losing distinct drawing locations
 -> engineer disposition
 -> publish versioned comments
```

Agents have narrow contracts:

| Agent/service | Input | Output | Mandatory guard |
|---|---|---|---|
| Drawing interpreter | Source regions, legend | Candidate entities/relations | No invented connections |
| Requirement resolver | Project metadata, approved standards | Applicable requirement references | Edition and scope verified |
| Piping reviewer | Confirmed topology, line list, rules | Potential issues/queries | Cite topology and rule |
| Instrument reviewer | Signals, index, control narrative | Loop/control discrepancies | No assumed protective function |
| Process reviewer | P&ID, datasheets, design basis | Contextual discrepancies | Calculations use approved tools |
| Evidence verifier | Findings and cited regions | Valid/rejected/needs-verification | Existence is not semantic support |
| Comment author | Verified observations | Concise location-linked comments | Separate fact, implication and action |

Deterministic rules should handle unique identifiers, reference integrity, explicit
attribute constraints, document consistency and machine-readable project requirements.
Contextual rules for drains, vents, isolation, bypasses, relief, and permissives require
project design context; they must not be universal assumptions.

Production comment contract:

- Finding ID, run ID and project ID.
- Drawing ID/revision, model snapshot and precise region(s).
- Equipment, port, line and instrument IDs as applicable.
- Observation, potential consequence, requested action and category.
- Requirement reference and applicability rationale.
- Supporting evidence, extraction uncertainty and limitations.
- Discipline, priority and rationale.
- Engineer disposition, reviewer identity, timestamps and supersession link.

The prototype implements only repeat-tag queries, documented-HOLD queries, submitted
specification conflicts, and optional evidence-cited agent review. It does not evaluate
an engineering standard or infer that a HOLD is an unresolved defect.

## 7. Equipment-specific detailed drawing workflow

1. Select the equipment on the source drawing; distinguish symbols from references.
2. Confirm identity, ports and connected topology.
3. Select boundaries using ports, isolation points and off-page connectors.
4. Retrieve equipment datasheets, line/valve lists, instrument index, control narrative,
   cause-and-effect documents, approved vendor drawings and project requirements.
5. Reconcile conflicting values using explicit project source precedence.
6. Build a specification matrix: established, conflicting, not applicable or missing.
7. Create a draft drawing model with source-derived geometry and approved symbol definitions.
8. If design proposals are allowed, create a **separate proposed-change set** with
   rationale, dependencies, required calculations and approval gates.
9. Render the source-backed draft and proposed overlay separately.
10. Validate topology and annotation fidelity, then require discipline approval.

Never silently convert a proposed value into a source fact. Do not invent dimensions,
ratings, materials, fail positions, flow directions, nozzle schedules or protective logic.
Critical design calculations must run through validated engineering tools and be reviewed
by qualified personnel.

The primary prototype authoring path now creates a newly composed PDF from a corrected
source-object model. Separate legacy paths provide source crops and generic SVG/PDF/JSON
detail sheets. None establishes a complete, standards-compliant P&ID automatically.
Native editable engineering output still requires selecting the target authoring tool
and implementing its symbol/attribute/connection API.

### Implemented source-object reconstruction and correction

The equipment type is open text, not a pump-only enumeration. Named package/tag discovery
is a navigation aid, while visual region selection identifies arbitrary depicted equipment.
The user must select its actual symbol or package outline rather than an off-sheet label.

The reconstruction model holds source symbol/lettering regions, independently chosen
destination positions, node ports, explicit connections and source stroke references.
Stroke-only compound paths are split into their original subpaths to separate unrelated
source lines. Native glyph/path objects are then relocated; new orthogonal connections
use source color, stroke width, dash, cap and join settings. Source template/notes regions
are reused separately. The output is a new sheet, not a complete network crop.

The automatic proposal handles horizontal vector crossings and nearby source lettering.
It does not claim semantic extraction of all branches, signals, valves or remote notes.
For the supplied HPU outline, the initial proposal finds six right-side interfaces;
left-side signal/drain details and other context still need engineer correction.
Supplier-defined package internals remain unknown.

The correction workspace supports source-symbol/annotation additions, element positioning
and deletion, editable connection/port data, and an original-object/stroke index. An
explicitly unconfirmed preview precedes user confirmation. Final snapshots are immutable,
source-checksummed and visibly marked not for construction. Removing the selected
equipment symbol, overlapping source assignments, unknown nodes, invalid stroke evidence,
unsupported grouped forms and stale document versions fail explicitly rather than
returning a crop or generic replacement symbol as a successful reconstruction.

Native same-sheet vector authoring is the current supported case. Scans, nested forms,
arbitrary filled compounds and robust automatic layout/semantic topology remain perception
and rendering work, not solved capabilities. Engineer confirmation is not a substitute
for an extraction benchmark or a formal engineering approval workflow.

### Implemented original-fidelity crop workflow

For the selected PDF-only requirement, a separate export path now preserves the original
page content instead of creating a generic diagram. Local connected-component image
processing proposes a boundary from line-bearing ink near confirmed equipment.
The engineer adjusts it, adds detached notes/specification crops, and confirms completeness.
PDF page boxes are changed using `pypdf`; original content streams, fonts and graphics
are retained without rasterization or rescaling. Each confirmed region becomes one page.

The source checksum, revision, graph version, exact region list and bounded extracted
text are persisted in an immutable provenance record. Live source checksums are validated
on export. No new engineering details are generated or inferred.

This is geometric line following, not a port-level semantic graph: crossings, dashed
signals, scan quality, detached symbols and remote notes remain limitations. The workflow
does not claim automatically complete equipment context. Source crop fidelity and
context-selection completeness are separate acceptance questions. Cropping is not redaction.

## 8. Azure production mapping

| Concern | Candidate service |
|---|---|
| API/workers | Azure Container Apps or an approved equivalent |
| Originals and derived artifacts | Azure Blob Storage with versioning and retention |
| Jobs | Service Bus plus idempotent workflow workers |
| Metadata/audit | Azure Database for PostgreSQL |
| Engineering graph | PostgreSQL graph tables initially; graph service only if justified |
| Reference retrieval | Azure AI Search with document/page/edition filters |
| OCR | Azure AI Document Intelligence, validated for the actual drawing set |
| Vision reasoning | Approved Azure model deployment with appropriate image support |
| Agent execution | Microsoft Agent Framework |
| Identity | Microsoft Entra ID and project-scoped authorization |
| Secrets | Managed identity and Key Vault |
| Monitoring | OpenTelemetry / Azure Monitor with source-content redaction |

All Azure services are a proposed deployment architecture, not provisioned resources.
Use approved regions, private networking, data retention rules and access controls.
Do not log full drawings or model prompts by default.

## 9. Reliability and trust boundaries

- Treat all document text, retrieved passages and model outputs as untrusted data.
- Enforce schema, identity, source revision and evidence relationships in code.
- Hash immutable sources; record parser, model, prompt, rule and legend versions per run.
- Use atomic artifact writes, idempotency keys and transactional publication.
- Checkpoint after extraction, corrected graph, review and package composition.
- Explicitly fail or mark a stage incomplete; never publish an empty successful result
  when a model/tool operation actually failed.
- Limit agent tool privileges and bounded loops; never let a drawing instruction
  authorize a tool call or change an engineering policy.
- Require project authorization and formal role-separated approval before release.

The local prototype has no production identity boundary. Bind to loopback only.
It rejects cross-origin browser requests and validates source references, but this
is not a substitute for an authenticated deployment.

## 10. Evaluation and release gates

Build a permissioned gold set with independent engineer annotations, including negative
examples and common drawing conventions. Split by project/drawing family, not random crops.

Measure:

- Symbol/tag precision and recall by class and scan quality.
- Port/connection precision and recall; crossing-versus-junction error rate.
- Off-page reference resolution accuracy.
- Specification extraction with units/context and source support.
- Finding precision, known-issue recall, unsupported-claim rate and location accuracy.
- Coverage by rule and applicable evidence, including abstentions.
- Generated graph-to-source fidelity, label preservation and missing-information disclosure.
- Engineer correction time, accepted/rejected comment rates, latency and cost.

Select quantitative thresholds with the responsible engineering organization before
deployment. A demonstration on one sheet cannot establish these rates. All claims in
released packages need traceability; any critical unsupported addition blocks release.
No autonomous construction approval is an acceptable benchmark shortcut.

## 11. Implementation stages and acceptance

1. **Local foundation (implemented):** readable-PDF import; source overlays; manual
   source-linked graph/specification correction; narrow structured review; offline
   SDK contract test; equipment package SVG/PDF/JSON; local persistence.
2. **Perception pilot:** approved symbol legend, OCR, symbol/port detection, line tracing,
   and engineer correction tools; pass a separately agreed annotated drawing benchmark.
3. **Engineering context:** reference-document ingestion, units/source precedence,
   standards applicability, port-level graph, cross-sheet navigation and validated rules.
4. **Authoring integration:** select CAD platform; implement native symbol placement,
   ports, line classes, editable labels, deterministic layout, and topology validation.
5. **Production governance:** Entra permissions, durable jobs, audit ledger, comment
   disposition, signed approvals, data lifecycle controls and operational evaluation.

Stages 2-5 require representative authorized drawings, legends, supporting documents,
engineering reviewers and selection of the target authoring environment.
