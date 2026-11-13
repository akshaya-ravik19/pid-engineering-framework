# P&ID Engineering Workspace

A local proof of concept for equipment-independent engineering drawing authoring and review.
Microsoft Agent Framework supplies the optional structured-output Azure reviewer.
Deterministic services handle source extraction, validation, graph traversal, persistence,
and draft rendering. This is **not** an autonomous engineering design or certification system.

## New equipment-interface P&ID reconstruction

**Drawing equipment / component** is a dropdown populated from the current document's
indexed labels and manually selected components. It is distinct from the manual-selection
type dropdown (pump, valve, vessel, instrument, package, etc.). It does not claim automatic
recognition of every depicted symbol. A sheet with no indexed labels displays an explicit
message and still supports direct drawing selection.

Choosing an indexed label locates it on the original page. If it has no engineer-defined
symbol outline, clicking **Propose equipment reconstruction** now starts outline selection
instead of sending an invalid reconstruction request. Draw the depicted symbol/package
outline, then click Propose again. The outline is attached to the existing component;
the original label/type are retained and no duplicate component is created.

The primary path now composes a **new sheet**. It does not export a rectangular equipment crop.
It relocates native source PDF symbols and lettering, reroutes engineer-verified connections
using source stroke/color/dash conventions, and reuses the source notes/title-block template.
The output is visibly marked as a derived draft, not a newly approved source revision.

1. Import/reimport the PDF. Updated text discovery includes named equipment/packages and
   additional tag conventions, not just pumps. Existing stored imports are not silently changed.
2. Click **Select equipment on drawing** in the viewer toolbar, then drag directly around
   the equipment on the original imported P&ID. **No equipment tag entry is required.**
   An unambiguous source label within the region is reused; otherwise an explicit region-based
   display name is assigned without guessing a tag. Cancel with the Cancel button or Escape.
   Select **any equipment type** using a tight source rectangle around the symbol or package
   outline. Component type accepts custom text. The rectangle defines a reusable vector symbol,
   not the output drawing's crop boundary. Do not select the complete network.
3. Click **Propose equipment reconstruction**. This creates a typed drawing model:
   independently positioned symbol/text nodes, ports, connections, style evidence and template.
4. Inspect the proposal against the source. Add missing valve/instrument symbols and annotations
   through region selection. Expand **Move or remove extracted elements** to change positions.
   Correct the connections, ports and source regions in the drawing-model editor.
5. Use **Source object / stroke index** to identify original vector paths and their styles when
   adding connections. Source indices refer to deterministic stroke-subpath separation.
6. Click **Preview reconstructed sheet**. This is explicitly unconfirmed and is not published
   as an engineer-confirmed result. Iterate on missing elements, labeling, layout and routing.
7. Only after verifying every required source connection, symbol, specification, flow indication
   and note, tick confirmation and generate the new equipment P&ID PDF.

The selected equipment, external interfaces and line annotations are relocated independently.
Original compound stroke paths are separated so four unrelated pipes stored as one PDF object
can become four individually routed connections. The renderer reuses native vector/text
objects, not screenshots or generic replacement equipment icons.

### Critical limits of the current reconstruction implementation

- **Equipment-independent authoring does not mean automatic recognition of every equipment.**
  Name/tag discovery is only a candidate index. Manual selection works for arbitrary equipment
  kinds on supported vector PDFs.
- Automatic proposals cover **horizontal vector boundary crossings** and nearby lettering.
  Vertical/curved lines, complete branch networks, valves, detached signals, flow arrows and
  remote specifications may be omitted. Correct them before generation. The source index and
  preview are provided for this purpose; a checkbox is not a completeness algorithm.
- The source HPU example produces an initial proposal of six right-hand hydraulic interfaces.
  That is **not the complete HPU drawing**: left-side signals/drain details and other context
  still require correction. Do not issue that initial proposal as an engineering deliverable.
- HPU/TUTU internals are supplier-defined on the supplied sheet. They cannot be reconstructed
  from information that is absent from the source.
- Only same-sheet native PDF objects are currently supported. Scanned pages, nested Form
  XObjects, rotated pages and nonzero source origins are not automatically reconstructed.
  Compound filled graphics crossing symbol selections may require different source regions.
- Source symbols/lettering remain at original scale. Routing is simple orthogonal routing;
  automatic layout does not yet resolve every collision or prove topology/semantic fidelity.
- Source template identifiers are retained as source references. The draft banner identifies
  the output as derived and not for construction; this is not formal title-block revisioning.
- Template reuse retains PDF content under clipping. This is not a secure redaction tool.

Generate a wholly synthetic, unconfirmed example:

```powershell
& '.\.venv-prototype\Scripts\python.exe' '.\examples\reconstruction_demo.py'
```

Compare `examples\exports\reconstruction-source.pdf` with
`examples\exports\reconstructed-interface-preview.pdf`. The model/provenance JSON
is also exported so the newly composed geometry is inspectable.

## Legacy original-fidelity equipment crop export

The legacy source-extract path **crops original PDF pages instead of reconstructing a drawing**.
Original vectors, fonts, colors, annotations, line styles, text and physical scale remain
in the exported PDF. Raster source content remains raster; it is not converted into vectors.
No new specifications, symbols, boilerplate or watermark are drawn over the source.
Source identity, revision and checksums are recorded in PDF metadata and provenance JSON.

1. Select the actual depicted equipment region with **Manual equipment selection**.
   A tag in an off-sheet reference is not sufficient.
2. Click **Propose connected boundary**. Local image processing follows connected ink
   containing linework near the selected source region, including attached branches/symbols.
3. Inspect the orange rectangle. Use **Draw replacement equipment boundary** or edit
   the normalized page/rectangle JSON to correct it.
4. Add detached specifications, notes, legends or another source page using
   **Add notes / specification region**. Each extra crop becomes another original-style page.
5. Preview all boundaries, explicitly confirm source completeness, then click
   **Export original-fidelity P&ID PDF**.

The first crop must contain the selected equipment's source region. Export requires a
current document version and explicit confirmation. Provenance JSON contains source-region
coordinates and automatically extracted text from those regions; this is not a semantic,
complete equipment specification database.

**Automatic tracing is geometric, not semantic P&ID interpretation.** It may merge
crossings, stop at gaps/dashed lines, include unrelated adjacent content, or omit
disconnected instrument signals and notes. No off-page references are automatically
resolved. A wide proposal is flagged for inspection. No nearby linework returns an
explicit error, not a fabricated boundary. Engineer confirmation is mandatory.

PDF cropping is a viewport operation, **not secure redaction**: cropped-out source
content may still be recoverable from the exported file. Treat exports as confidential
source documents. The format is a crop, not a new full-size title-block sheet.

The synthetic demonstration now also writes `examples\exports\synthetic-original-pid.pdf`
and its provenance JSON. The older generic schematic remains available in a collapsed
optional section and is explicitly not original-fidelity output.

## Run on this machine

Open PowerShell:

```powershell
Set-Location -LiteralPath 'C:\Users\aravikumar4\OneDrive - TEN\P&ID Framework'
& '.\.venv-prototype\Scripts\python.exe' -m pid_framework serve
```

Open <http://127.0.0.1:8765>. Stop with Ctrl+C. The server binds to loopback only.
The existing `.venv` could not be synchronized because of an access-denied error;
the runnable implementation uses its own `.venv-prototype` environment instead.

The supplied PDF and a clearly synthetic demonstration are already imported.
Example SVG/PDF/JSON drafts are under `examples\exports`. Recreate the synthetic
demonstration using:

```powershell
& '.\.venv-prototype\Scripts\python.exe' '.\examples\demo.py'
```

For a fresh installation with Python 3.12+ and `uv`:

```powershell
$env:UV_PROJECT_ENVIRONMENT='.venv-prototype'
uv sync --group dev --link-mode copy
```

## What works

- Import PDFs through the browser or CLI; originals remain local under `data`.
- Extract positioned pump-tag text candidates and HOLD occurrences from readable text layers.
- Navigate pages, zoom/pan, search tags, inspect source overlays, and open the original.
- Manually select a depicted component and source region; add pumps, valves,
  instruments, equipment, and package boundaries.
- Enter source-linked specifications and edit an explicitly confirmed connection graph.
- Produce structured review queries for repeated tag text and documented HOLD occurrences.
- Flag conflicting submitted specification values; this requires engineer-entered data.
- Optionally call an Azure reviewer using the actual Microsoft Agent Framework SDK.
- Generate immutable equipment packages with confirmed connected components,
  traversal boundaries, source crops, specification schedules, explicit unknowns,
  proposed review tasks, and SVG/PDF/JSON exports.
- Persist documents, graph versions, review snapshots, and packages in SQLite.
  Stale graph edits are rejected rather than silently overwriting newer edits.

## Suggested walkthrough

1. Reopen the imported sample from **Stored documents**, or upload a PDF and specify its revision.
2. Click a candidate. Check whether it is depicted equipment or an off-sheet reference.
3. If it is only a reference, **do not confirm it as depicted equipment**. Obtain the referenced
   drawing or use manual region selection on an actual depicted component.
4. Enter a component tag/type, click **Select region on drawing**, and drag a rectangle.
   The source region is recorded as an engineer selection, not automatic interpretation.
5. Add specifications in the selected equipment's JSON editor, for example:

   ```json
   [
     {
       "name": "Design pressure",
       "value": "Value transcribed from the selected source region",
       "evidence_ids": ["selection-REPLACE-WITH-ACTUAL-ID"]
     }
   ]
   ```

   The document/evidence JSON link lists available IDs. Do not invent values.
   A valid evidence reference does not prove that the claimed value is semantically correct.
6. To connect two selected components, add an entry in the graph editor:

   ```json
   {
     "id": "connection-1",
     "source": "REPLACE-WITH-EQUIPMENT-ID",
     "target": "REPLACE-WITH-OTHER-EQUIPMENT-ID",
     "description": "Connection verified by engineer in the cited source region",
     "evidence_ids": ["REPLACE-WITH-EVIDENCE-ID"],
     "confirmed": true
   }
   ```

7. Save the graph, run review, and click evidence buttons to locate comments.
8. Select equipment and generate a draft. Export SVG, PDF, and package JSON.
   Include proposed review tasks if desired; they are not designed physical additions.

## Local sample import

```powershell
& '.\.venv-prototype\Scripts\python.exe' -m pid_framework import-pdf `
  'C:\Users\aravikumar4\Downloads\MZLN-JJ-PDPID-11-0560-001_A.pdf' --revision A
```

The supplied sample has one readable page. Local extraction identified three pump-tag
references and 44 HOLD text occurrences. These counts do not represent three depicted
pumps or 44 technical defects. No source drawing content was sent to an external model
in sample validation. Source drawing content is not committed to this project.

## Optional Azure reviewer

Do not enable this for proprietary drawings without the data owner's authorization and an
approved Azure deployment. Enabling it sends the extracted document records, annotations,
and source-linked entries to that deployment; it does not send original PDF bytes.

Copy `.env.example` to `.env`, configure endpoint/deployment, authenticate using an approved
`DefaultAzureCredential` method, and explicitly set `PID_ENABLE_MODEL_CALLS=true`.
Then select the Azure checkbox for a review. Neither configuration nor the checkbox
alone is enough: both are required. No keys belong in source control.

Azure credentials and a live deployment were not supplied. The SDK path is tested with
mocked HTTP responses; live Azure behavior and engineering accuracy are not verified.

## Limits you must understand

- No OCR for image-only pages; an explicit limitation is returned.
- Rotated pages must be normalized before import. PDFs: 32 MiB / 50-page limits.
- No automatic pump-symbol recognition, semantic piping tracing, junction interpretation,
  off-sheet resolution, or mapping to a project symbol legend.
- Tag detection is a narrow, configurable-in-code prototype convention, not a universal parser.
- No datasheet ingestion or standards retrieval yet. Specifications are source-linked manual
  entries. The checklist does not automatically establish completeness.
- The optional generic detail export is a source-backed detail sheet plus generic
  connectivity sketch, **not** a standards-compliant native CAD P&ID. This older export
  supports at most 12 components. Original-fidelity crop export has a 20-region limit.
- Proposed additions are review tasks only. Autonomous piping design, equipment sizing,
  protective-function design, and construction approval are intentionally not implemented.
- No production authentication, durable distributed jobs, formal approval signatures,
  role separation, or finding disposition workflow. Do not expose this service to a network.
- Local PDFs, images, database records and exports are confidential engineering data.
  Apply appropriate filesystem permissions, retention, backup, and organizational controls.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the production framework design and implementation stages.

## Validation

```powershell
& '.\.venv-prototype\Scripts\python.exe' -m pytest -q
& '.\.venv-prototype\Scripts\ruff.exe' check src tests
& '.\.venv-prototype\Scripts\ruff.exe' format --check src tests
```

Tests use synthetic drawings and offline SDK responses. They cover extraction locations,
graph validation, source references, explicit missing OCR, package boundaries, candidate
confirmation, unknown specifications, SVG/PDF output, conflict queries, version conflicts,
persistence across app instances, disabled model access, and cross-origin rejection.
Original-fidelity tests additionally check actual rendered-pixel agreement, unchanged
source content streams, physical dimensions, repeated crops of the same page, nonzero
source cropboxes, connected branch tracing, missing-linework errors, engineer confirmation,
source checksum protection, and persistent provenance.
