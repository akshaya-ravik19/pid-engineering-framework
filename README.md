# Equipment P&ID Generator

A local engineering workspace that generates a new equipment-focused P&ID draft from an
uploaded P&ID PDF.

**Upload PDF -> Select equipment -> Generate connected-system P&ID**

The output includes the selected equipment and its **traceable connected system on the
same source page**: supporting symbols, piping, branches, and nearby source lettering.
Recognized off-page continuations become labeled boundaries; other pages are not followed.

This is **not a screenshot or rectangular crop**. Supported native PDF symbols and text are
independently relocated, spacing is increased, and pipelines are rerouted on a new sheet.
The uploaded drawing is never modified.

> All outputs are unapproved drafts, **not for construction**. Geometric connectivity is
> not verified process connectivity or proof of a complete, correct P&ID.

## Documentation

| Document | Audience and purpose |
|---|---|
| [User guide](./USER_GUIDE.md) | Screen navigation, equipment selection, outputs, and troubleshooting |
| [Architecture](./ARCHITECTURE.md) | Current components, tracing, agents, data contracts, and trust boundaries |
| [Azure deployment guide](./AZURE_DEPLOYMENT.md) | Optional agent activation and private-deployment prerequisites |

## Start the application

From the project directory, using the existing project environment:

```powershell
& '.\.venv-prototype\Scripts\python.exe' -m pid_framework serve
```

Open **http://127.0.0.1:8765**. Keep the terminal running while using the application.
Restart the server and refresh the browser after updating application code.

This command assumes the environment and dependencies are already installed. For a new
environment, Python 3.12 or later and the dependencies declared in
[pyproject.toml](./pyproject.toml) are required.

## Use the framework

1. Choose your P&ID PDF and click **Upload PDF**.
2. Navigate with **Page**, **+**, and **-** in the original-drawing viewer.
3. Click **Select equipment on drawing** and drag tightly around the equipment symbol or
   package outline. Include its label when possible; no typed tag is required.
4. Click **Generate equipment P&ID**.
5. Inspect the new drawing, **System boundaries**, **Source information**, and
   **Coverage and uncertainty**.
6. Select **Download drawing + explanation PDF**. Use the vector PDF for detailed zoom
   on large systems; the browser preview is an overview.

**Detected source labels** is an optional navigation aid, not a complete equipment inventory.
If a chosen label has no symbol outline, clicking Generate starts guided outline selection.
After drawing that outline, click Generate again.

See the [user guide](./USER_GUIDE.md) for the full walkthrough.

## What is included

- PDF-only upload, original-page viewing, zoom, and direct equipment-region selection.
- Same-page tracing through supported straight vector segments, bends, endpoint contacts,
  and T-junctions, including vertical and diagonal linework.
- Supporting-symbol candidates from closed/filled source paths; touching strokes such as
  valve halves are grouped. Nearby text is associated geometrically.
- Connected-system traversal beyond the first neighboring item. Disconnected systems and
  interior-only line crossings are excluded by the tracing rules.
- Boundary markers such as `B1` for recognized continuations or unresolved source line ends.
- A newly laid-out PDF that retains original symbol scale and expands the sheet when needed.
- Plain-language output explanations, source-page references, checksum/version provenance,
  explicit limitations, and optional AI-assisted explanations.
- Local persistence of uploaded sources, document records, selections, and generation models.

There is **no user-facing technical-review/correction workflow**, drawing-model editor,
crop export, generic sketch export, or stored-document chooser.

## Microsoft Agent Framework

Local generation works without Azure and makes no AI calls. When Azure settings are present
and model calls are enabled, the optional **Use configured Microsoft agents** checkbox is
shown. It is unchecked by default and requires authorization to send extracted records.

Three Microsoft Agent Framework `Agent` instances run sequentially:

| Agent | Responsibility | Enforced boundary |
|---|---|---|
| `SourceEvidenceAgent` | Select relevant existing source evidence | No duplicate or unknown evidence IDs |
| `DrawingLayoutAgent` | Position the existing connected-system nodes | No added/removed nodes or topology changes; bounds and node overlaps checked |
| `DrawingExplanationAgent` | Explain the selected source evidence | Each explanation must cite evidence from the selected context |

All three use the **same configured Azure deployment**, `PID_AZURE_DEPLOYMENT`; no model
is hardcoded. Ordinary Python code orchestrates the typed handoffs. There is no active
LangGraph runtime, autonomous tool-use loop, or separately registered Foundry hosted agent.

The models receive structured records, **not PDF images**. Deterministic code performs
source parsing, network tracing, rendering, and persistence. Reference validation does not
prove that an AI explanation is semantically correct. Agent errors abort that generation;
they are not silently replaced by local success.

See [agent activation and private deployment](./AZURE_DEPLOYMENT.md). No live Azure
deployment or model invocation is established by this documentation.

## Outputs and storage

| Output | Contents |
|---|---|
| Drawing preview | Overview of the newly arranged connected system |
| Drawing + explanation PDF | Vector drawing first, then reading guidance, source evidence, boundaries, and limitations |
| Source and generation details | JSON with drawing geometry, evidence, document version/checksum, and agent/deployment information |

Sources are stored under checksum-based names in `data`; metadata and generation models
are stored in `data/framework.sqlite3`. Downloads are rendered from those saved models.
The browser starts with an upload screen even though saved data persists. A refresh is
**not** a delete operation, and there is no user-facing deletion/retention manager.

## Supported inputs and limits

- Uploads must be `.pdf`, at most **32 MiB** and **50 pages**. File checks do not certify
  that an arbitrary PDF is actually a P&ID.
- Generation targets supported same-page native vector content. OCR and complete visual
  equipment recognition are not implemented. Scanned/image-only drawings, grouped Form
  XObjects, rotated pages, and nonzero source origins are not supported for this workflow.
- Tracing covers reachable **supported geometry**, not guaranteed complete plant topology.
  Gaps over **0.8 PDF points**, open curved paths, unrecognized/open symbols, crossing
  conventions, detached notes, and unrecognized continuations can cause omissions or
  misinterpretation. Open symbols may be treated as linework.
- Supplier-defined internals or specifications absent from the source are not invented.
  Nearby text is source context, not a verified engineering specification association.
- Source-page tracing limits are 20,000 objects, 1,000 candidate symbols, and 6,000 straight
  segments. Output limits are 500 nodes, 1,000 connections, and 14,400 points per sheet
  dimension. Exceeding a limit fails explicitly rather than silently truncating the system.
- Node bounds/overlaps are validated, but routing is simple orthogonal routing, not a proof
  of collision-free lines, correct process semantics, standards compliance, or design approval.
- With no supported connections, a symbol-only draft can be generated; it explicitly says
  it is not a complete P&ID.
- This is a localhost prototype, not an authenticated production service.

## Developer reference

The [architecture](./ARCHITECTURE.md) describes the API and module responsibilities.
Main generation endpoint:

```text
POST /documents/{document_id}/generations
{
  "equipment_id": "<selected equipment ID>",
  "expected_version": <current document version>,
  "use_agents": false
}
```

Run the existing validation tools:

```powershell
& '.\.venv-prototype\Scripts\python.exe' -m pytest -q
& '.\.venv-prototype\Scripts\ruff.exe' check src tests
& '.\.venv-prototype\Scripts\ruff.exe' format --check src tests
```

Tests create synthetic inputs in memory or temporary directories; no example PDF is
preloaded into the application. Legacy geometry/review helpers remain internal for
regression purposes, not as alternative user-facing workflows.
