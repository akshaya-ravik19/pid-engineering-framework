# Equipment P&ID Generator: current architecture

This document describes the **implemented connected-system generator**, not the earlier
review/correction design. For operation, see the [user guide](./USER_GUIDE.md); for a quick
start, see [README](./README.md).

## 1. Scope and responsibility

The application accepts a P&ID PDF, lets an engineer select an actual equipment symbol,
and generates a newly laid-out draft of its traceable connected system on that source page.
It preserves supported original symbols/lettering, reroutes piping, and explains source
context, boundaries, and uncertainty.

It does not modify the upload, recommend drawing corrections, perform technical compliance
review, or expose a drawing-model editor. Cropped-page and generic-sketch exports are not
active API/UI workflows. It does not certify completeness, safety, or engineering correctness.

## 2. Component map

```text
Browser: PDF upload -> source viewer -> equipment selection -> Generate
                              |
                       FastAPI, loopback only
                              |
             Source PDF + document/evidence records
                              |
           Deterministic same-page vector network tracing
                              |
          Connected drawing model + source-text evidence
                              |
             +----------------+----------------+
             |                                 |
       Local generation                 Optional Azure agents
       initial layout                 Evidence -> Layout -> Explanation
             |                                 |
             +----------------+----------------+
                              |
           Layout / reference / native-object validation
                              |
             New vector sheet + explanation-page rendering
                              |
                 Save generation model after rendering succeeds
                              |
               Preview PNG / PDF download / JSON details
```

Local parsing, graph construction, drawing composition, and storage are custom application
code. Microsoft Agent Framework supplies the optional bounded model interactions.

### Main modules

| Module | Responsibility |
|---|---|
| [api.py](./src/pid_framework/api.py) | Upload, selection, generation, reads/downloads, HTTP validation and local-request policy |
| [web/index.html](./src/pid_framework/web/index.html), [web/app.js](./src/pid_framework/web/app.js) | Three-step UI, source navigation, pointer selection, preview, output explanations |
| [pdf.py](./src/pid_framework/pdf.py) | Positioned text/tag candidate extraction, source-page rendering, shared PDFium lock |
| [connected.py](./src/pid_framework/connected.py) | Supporting-symbol candidates, line graph, same-page traversal, boundaries, expanded layout |
| [generation.py](./src/pid_framework/generation.py) | Generation orchestration, bounded source text, optional agents, output explanation and combined PDF |
| [agents.py](./src/pid_framework/agents.py) | Azure settings/authentication, three SDK agents, evidence/layout response validation |
| [reconstruction.py](./src/pid_framework/reconstruction.py) | Shared native-object isolation, source checks, symbol relocation, source-style connection rendering |
| [models.py](./src/pid_framework/models.py) | Typed document, evidence, drawing, generation, and agent contracts |
| [store.py](./src/pid_framework/store.py) | Local SQLite records, document version updates, checksum-based source files |
| [__main__.py](./src/pid_framework/__main__.py) | Local server and PDF-import CLI |

Legacy helpers in the repository are not evidence of an exposed review, crop, or editing
feature. The route definitions and browser workflow determine the active application surface.

## 3. Upload and selection

1. The browser sends raw PDF bytes, not a multipart form, with a filename.
2. The API checks the `.pdf` extension, `application/pdf` content type, PDF header, and
   32 MiB size limit. Extraction accepts at most 50 pages and 200,000 text characters per page.
3. Positioned text matching builds a candidate equipment/label index and source evidence.
   This is not complete equipment recognition; an index can be empty on a usable vector page.
4. Source bytes are stored by SHA256 and a `Document` is saved.
5. The user selects a source rectangle around the actual equipment. A label choice alone
   does not establish a symbol outline.
6. A selection records `engineer_selection` evidence, adds or updates an equipment record,
   and advances the document version. It does not approve the design.

An unambiguous indexed label inside a new selection can supply its display tag. Otherwise,
the system uses an explicit page/selection-based name rather than inventing an equipment tag.
When updating an existing equipment selection, the latest outline supersedes earlier
outline references for that equipment.

The browser supports direct new selection or guided outline capture after choosing an
indexed label. It exposes no graph/specification correction forms.

## 4. Connected-system tracing

### 4.1 Source-object preparation

- Use native top-level PDF objects from the selected source page.
- Split compound stroke-only paths into subpaths so independent pipes are not one
  indivisible source object. Extraction and rendering use the same deterministic splitting.
- Use the selected equipment outline as the traversal seed.
- Identify other closed or filled path objects as supporting-symbol candidates. Touching
  candidates can be grouped, for example the two halves of a valve.
- Associate enclosed native objects and nearby text with candidate symbols.
- Recognize some continuation symbols using nearby text patterns such as `TO DWG`,
  `FROM SHEET`, `OFF PAGE`, or `CONTINUED ON`.

These are geometric heuristics, not an equipment classifier or a learned symbol recognizer.
Closed piping loops or unrecognized glyphs can be misclassified. Package internals are
reused only when present in the selected/native source content; absent internals are unknown.

### 4.2 Line graph

Open stroked straight segments provide candidate pipeline/signal linework. Horizontal,
vertical, diagonal, and bent paths are supported. Open curved paths are not traced.

The tracer:

1. Splits linework at symbol-region boundaries and endpoint contacts.
2. Detects endpoint-to-segment contacts, including T-junctions, within 0.8 PDF points.
3. Creates symbol and junction nodes with source object/segment references.
4. Builds adjacency and traverses reachable connections from the selected equipment.
5. Stops traversal through recognized continuation-boundary nodes.
6. Discards unreached symbols and linework.

An interior-only crossing, without endpoint contact or an intervening recognized symbol,
does not create a junction. This convention reduces accidental inclusion of unrelated
systems but does not resolve every engineering drawing's crossing/junction conventions.
Connection traversal is not a verified upstream/downstream or flow-direction analysis.

### 4.3 Boundaries and annotations

Reached continuation symbols and terminal unresolved junctions receive `B1`, `B2`, etc.
Their descriptions explain whether the marker is a recognized continuation or an unresolved
source line end. No other page is traversed, even if a destination drawing is named.

Text inside retained symbol regions and lettering near retained segments are included
where supported. Connection source endpoints bound the retained segment context, rather
than treating an entire original path as relevant after a continuation boundary.
Proximity is not proof that a note/specification applies to a particular equipment item.

## 5. Layout and drawing composition

The initial layout expands source-relative node-center spacing by a factor of 1.7. It
retains original glyph scale and grows the destination sheet as needed. This is a
deterministic spatial expansion, not an optimal graph-layout or collision-free routing engine.

The renderer:

- Relocates assigned native source objects individually using PDF transformation matrices.
- Does not substitute a rectangular source-page crop or screenshot.
- Creates new orthogonal connections between retained node ports.
- Reuses cited source stroke color, width, dash pattern, line cap, and line join.
- Labels system boundaries and places a visible draft/not-for-construction banner.
- Produces a newly sized PDF sheet; source template/title-block regions are not automatically
  carried into this connected-system workflow.

Layout validation checks node identity, bounds, and overlapping node extents. Native
rendering validates source checksum/dimensions, selected-symbol retention, source-object
assignments, and connection style references. These checks do not prove semantic port
identification, line/label clearance, topology fidelity, or process correctness.

A combined PDF contains the drawing first, followed by explanation pages with source
references, reading guidance, boundary descriptions, generation mode, and limitations.
The browser preview is a raster overview of this generated vector sheet, not the method
used to construct the downloaded drawing.

## 6. Optional Microsoft Agent Framework pipeline

The application uses actual Microsoft Agent Framework `Agent` objects and typed responses.
Sequential Python calls orchestrate the workflow; there is no LangGraph runtime,
autonomous tool loop, distributed workflow checkpointing, or registered Foundry hosted agent.

| Stage | Receives | Returns | Enforced checks |
|---|---|---|---|
| `SourceEvidenceAgent` | Equipment name and bounded source evidence | `EvidenceContext` | Existing IDs only, no duplicate IDs |
| `DrawingLayoutAgent` | Existing connected drawing model, source/destination sizes | `DrawingLayout` | Exactly one position for each existing node, in-bounds extents, no node overlaps |
| `DrawingExplanationAgent` | Equipment name and evidence selected by stage 1 | `GenerationExplanation` | Explanation citations belong to that selected evidence context |

The layout agent cannot change geometry, ports, edges, specifications, or source references:
its output contract permits only existing node IDs and positions. The explanation agent
does not determine the network. All deterministic source evidence remains in the output
even when the evidence agent selects a narrower explanation context.

Instructions prohibit invented specifications, corrections, approval claims, and treating
source strings as instructions. These instructions and valid citations do not establish
semantic truth; AI explanations remain explicitly unverified.

All agents share one `OpenAIChatCompletionClient` configured with `PID_AZURE_DEPLOYMENT`.
No model name is hardcoded. `DefaultAzureCredential` supplies Azure authentication.
See [Azure activation](./AZURE_DEPLOYMENT.md) for endpoint and deployment prerequisites.

Model calls require both enabled/configured settings and per-generation user opt-in.
Structured source records, not original PDF images, are sent; those records can still be
confidential. Model/schema/reference failures abort the operation without silently
substituting local success. The user can explicitly choose local mode for a separate attempt.

## 7. Data contracts and persistence

Coordinates use one-based page numbers and normalized top-left rectangles
`[left, top, right, bottom]`. Source and destination page sizes are PDF points.

| Contract | Main contents |
|---|---|
| `Document` | ID, filename, revision, checksum, pages, evidence, equipment/connection records, version, limitations |
| `Evidence` | ID, page/rectangle, source text or selection description, origin |
| `DrawingNode` | Source region, label/kind, destination position, optional explicit source object IDs |
| `DrawingConnection` | Node/port references, source object/style evidence, optional source segment endpoints |
| `ReconstructionModel` | Connected nodes/edges, source/destination sizes, warnings; internal name retained for compatibility |
| `GeneratedDrawing` | Document/version, selected equipment, drawing snapshot, source evidence, explanations, agent names/deployment |

Generation keeps `engineer_confirmed=false` and uses status
`generated_draft_not_for_construction`. A successful generation is not design approval.

Local storage:

- `data/<SHA256>.pdf`: source bytes, checked against the document checksum.
- `data/framework.sqlite3`: JSON payloads in the `records` table; active kinds include
  `document` and `generation`.
- Document selection updates use transactions and expected-version checks.
- Generation performs a render before saving its result, so a failed native render does
  not publish a successful generation record.

Saved drawing models and source provenance are snapshots. PDF and preview downloads are
rendered again from the saved model/source; PDF bytes are not archived as an immutable
signed deliverable. Parser/prompt versions and a formal approval audit ledger are not
currently recorded as a production governance system.

Data persists across server restarts and browser refreshes. There is no stored-document
chooser or user-facing delete/retention manager; an upload screen does not imply empty storage.

## 8. Active API

| Method and route | Purpose |
|---|---|
| `GET /`, `GET /app.js` | Browser interface |
| `GET /health` | Local readiness/configuration flags; not a live Azure inference test |
| `POST /documents?filename=<name>.pdf&revision=<optional>` | Raw PDF upload; revision defaults to `UNVERIFIED` |
| `GET /documents`, `GET /documents/{id}` | Saved document metadata |
| `GET /documents/{id}/source` | Original PDF |
| `GET /documents/{id}/pages/{page}.png` | Original-page viewing image |
| `POST /documents/{id}/selections` | Source outline and current expected version; optional existing equipment ID |
| `POST /documents/{id}/generations` | Selected equipment ID, expected version, optional `use_agents` |
| `GET /generations/{id}` | Saved generation JSON |
| `GET /generations/{id}/drawing.pdf` | Drawing plus explanation PDF |
| `GET /generations/{id}/preview.png` | Generated drawing overview |

Review, graph-update, proposal/editor-preview, crop-export, and generic-package routes are
not exposed. Typical errors are 404 for absent records, 409 for stale versions, 413 for
oversized uploads, 415 for unsupported upload format, 422 for invalid/unsupported drawing
data, and 503 for explicitly requested but disabled/incomplete Azure configuration.
Unexpected failures return a generic 500 response and are logged by the local server.

## 9. Reliability, limits, and trust boundaries

PDFium calls share a process-local reentrant lock because the library is not thread-safe.
This is not a distributed lock or production background-job queue.

| Limit | Current value |
|---|---|
| Upload bytes / pages | 32 MiB / 50 |
| Text characters per page | 200,000 |
| Objects / candidate symbols / straight segments per traced page | 20,000 / 1,000 / 6,000 |
| Output nodes / connections | 500 / 1,000 |
| Sheet dimensions | 1 to 14,400 points per dimension |

Exceeding limits fails explicitly. No silent truncation is used to claim a complete system.
Image-only drawings, grouped forms, rotated pages, and nonzero source origins are not
supported for this native-object workflow. Open curves, gaps, ambiguous symbols/junction
conventions, and detached context remain coverage limits. A source with no supported
connections can yield an explicitly incomplete symbol-only draft.

The server binds to loopback, restricts trusted hosts, rejects cross-origin browser
requests, and sets no-store/CSP/content-type protection headers. These controls are not
authentication, document authorization, a hardened PDF sandbox, or a production security boundary.
Originals, records, exports, and extracted model inputs must be handled under the owner's
confidentiality policy.

## 10. Validation and deployment status

Existing automated tests cover upload/selection/version behavior, typed agent handoffs
with mocked model responses through the SDK, invalid citations/layouts, failure handling,
source fidelity, and native PDF rendering. Connected-system cases include a multi-hop
pump/valve/vessel network, a vertical instrument branch, excluded disconnected crossings,
off-page stopping, expanded sheets, and retained source lettering/glyph scale.

Synthetic regression tests are not a representative engineering accuracy benchmark.
They do not establish symbol recall, complete topology recovery, or compliance coverage.
Live Azure model behavior requires separate authorized synthetic smoke testing.

The complete application is not deployed to Foundry/Azure. Private cloud hosting would need
approved application ingress, Entra authentication/authorization, durable private storage
and database services, managed identity, private AI connectivity, and operational controls.
Foundry AI hosting does not automatically host the browser/PDF application.

See [private deployment prerequisites](./AZURE_DEPLOYMENT.md). OCR, semantic symbol/port
recognition, verified cross-sheet traversal, standards reasoning, native CAD authoring, and
formal approval workflows are **not implemented capabilities** of this version.
