# User guide: Equipment P&ID Generator

This guide explains how to upload a P&ID, select equipment, and read a newly generated
connected-system drawing. No programming or JSON editing is needed.

**The three steps:** Upload PDF -> Select equipment -> Generate equipment P&ID.

> The output is a source-supported draft, **not for construction or design approval**.
> It does not recommend corrections to your uploaded drawing or establish that every
> connection has been recovered.

## 1. Before you start

> If startup reports that `DraftReviewRequest` or `DraftReviewEvent` cannot be imported,
> the checkout contains unfinished draft-review code. This is a server startup issue,
> not a problem with your PDF or Azure credentials. The steps below apply once the server
> starts successfully.

- Use a P&ID PDF that you are authorized to process.
- The maximum upload size is **32 MiB**, with at most **50 pages**.
- Use a supported vector PDF: its lines, symbols, and text should be native PDF content.
  A scanned page can look clear but still be only an image. OCR is not implemented.
- Generation works on the page containing the selected equipment. It does not combine
  systems across multiple source pages.
- Local generation does not need an Azure account or an AI model.
- Do not enable Azure agents for confidential records without the necessary authorization.

If the server is not running, open a terminal in the project directory and run:

```powershell
& '.\.venv-prototype\Scripts\python.exe' -m pid_framework serve
```

This assumes the existing project environment is installed. Keep the terminal open and
visit **http://127.0.0.1:8765**. For environment questions, see [README](./README.md).

## 2. Understand the screen

| Area | What it is for |
|---|---|
| **1. Upload your P&ID** | Choose and upload your source PDF |
| **2. Select equipment** | Use detected labels or select a symbol directly |
| **3. Generate new P&ID** | Generate locally or optionally use configured Microsoft agents |
| **Original P&ID** | View source pages, zoom, and draw the equipment selection |
| Status message near the top | Progress, selection instructions, or an error |
| **Equipment P&ID draft** | Generated overview, downloads, source information, boundaries, and limitations |

The output section appears after successful generation. There is no sample gallery,
stored-document chooser, correction editor, or crop-export option.

## 3. Upload your P&ID PDF

1. Under **1. Upload your P&ID**, choose your `.pdf` file.
2. Click **Upload PDF**.
3. Wait for the upload-complete message and the original drawing to appear.
4. Check the displayed filename.

The application checks the file format and limits. It does not determine whether an
arbitrary PDF is a genuine P&ID; make sure you selected the right drawing and revision.
The browser workflow records the revision as `UNVERIFIED`, not an approved revision.

## 4. Navigate the original drawing

- Use **Page** to switch source pages.
- Use **+** and **-** to zoom the source viewer.
- Scroll inside the viewer to move around an enlarged page.
- Use **Open original PDF** to view the uploaded PDF separately.
- Optionally choose an entry under **Detected source labels** to locate a candidate.

Detected labels are not a complete equipment list. Some are references to equipment on
another sheet rather than equipment depicted on the current page. An empty dropdown does
not mean the drawing has no equipment: direct symbol selection is still available.

## 5. Select the equipment

### Option A: Select directly on the drawing

1. Navigate to the equipment's page and zoom until its symbol is clear.
2. Click **Select equipment on drawing**.
3. Drag a rectangle tightly around the actual equipment symbol or package outline.
4. Include its tag inside the rectangle when practical, without enclosing the whole network.
5. Release the pointer and wait for **Equipment selected**.

You do not need to type a tag or draw a boundary around all supporting equipment. The
rectangle identifies the central source symbol; the framework traces supported connected
linework beyond it. It is **not** the crop boundary for the output.

If the source label is ambiguous or not indexed, a name such as
`Selected equipment on page 1 (...)` is used. This is an explicit selection name, not an
invented plant tag. Original source lettering can still appear in the generated drawing.

### Option B: Start from a detected label

1. Choose the intended entry under **Detected source labels**.
2. Check that the highlighted location really belongs to the equipment you want.
3. Click **Generate equipment P&ID**.
4. If the equipment has no saved symbol outline, the application asks you to drag its
   actual symbol/package outline. Complete that selection.
5. Click **Generate equipment P&ID** again.

Choosing a label alone does not provide the geometry needed to create the new drawing.

### Cancel or choose another item

- While selecting, click **Cancel selection** or press **Escape**.
- To select a different item directly, click **Select equipment on drawing** again and
  draw its outline. This begins a new direct selection.
- To return to an existing selected item, choose its entry in **Detected source labels**.

## 6. Choose local or optional agent-assisted generation

### Local mode

Leave **Use configured Microsoft agents** unchecked. The framework extracts supported
source objects, traces the connected network, lays it out, and creates the PDF locally.
The output identifies that no AI agents were invoked.

### Agent-assisted mode

The checkbox is visible only when Azure model calls are enabled and endpoint/deployment
settings are present. Check it only if you are authorized to send extracted source records.

Three Microsoft Agent Framework agents run:

1. **SourceEvidenceAgent:** selects relevant existing evidence.
2. **DrawingLayoutAgent:** positions existing drawing elements.
3. **DrawingExplanationAgent:** explains selected source evidence.

They share the configured Azure model deployment. They receive structured records, not
PDF images, and do not provide full visual equipment recognition or invent missing internals.
The output labels AI explanations as not independently verified.

The layout agent can move existing symbols on the new sheet, but it does not find missing
equipment or change which items connect. The browser has no draft-review or approval
controls. Compare the output with the original using your normal engineering review process.

If the checkbox is absent, local generation is still available. An administrator can use
the [Azure activation guide](./AZURE_DEPLOYMENT.md). A visible checkbox means settings are
present, not that credentials, networking, quota, or live inference have been verified.

## 7. Generate the new P&ID

1. Check that the intended equipment is selected.
2. Click **Generate equipment P&ID**.
3. Wait for the success message. Controls are temporarily disabled during processing.
4. Inspect the **Equipment P&ID draft** section.

The framework attempts to include all reachable **supported vector geometry on that page**:

- The selected equipment.
- Connected supporting symbols, such as recognized equipment, valves, and instruments.
- Traceable piping segments, bends, and branches.
- Nearby source labels and annotations.
- Recognized off-page continuation symbols and unresolved line ends as labeled boundaries.

Supporting objects are independently positioned, spacing is increased, and pipelines are
rerouted. The sheet can grow to keep source symbols at their original scale.
The original PDF is unchanged.

**Important:** this means all traceable supported geometry, not a guarantee of every real
plant connection. Interior-only line crossings do not automatically join. Gaps, unusual
symbols, curved paths, scans, or drawing conventions can limit the result.

## 8. Interpret the output

### Drawing overview

The preview shows the newly generated sheet. It is an overview image of a vector drawing,
not a screenshot of the selected source region.

For a large system, download the PDF and zoom in. Do not judge label readability solely
from a fit-to-screen browser overview.

### System boundaries: B1, B2, and so on

Read each marker together with its entry under **System boundaries**.

| Marker description | Meaning |
|---|---|
| Recognized continuation, for example `TO DWG 2` | The source indicates a continuation; traversal stops there |
| `Unresolved source line end` | Supported tracing ended at that location; it is not proof of a physical pipe termination |

The framework does not follow other source pages or invent a missing destination.
A B marker is neither a defect finding nor a recommended correction.

### Source information

This lists extracted source text and selection information with page references. Use it
to understand the evidence behind the drawing. A nearby pressure value or note is not
automatically verified as applicable to one particular equipment item.

### AI-assisted explanations

This section appears when the optional agents return explanation items. Each explanation
cites source evidence. Valid references do not guarantee a correct engineering interpretation.
An absent/empty AI explanation section does not imply that the drawing passed a review.

### Coverage and uncertainty

Read this section for unsupported geometry, possible omissions, and interpretation limits.
A symbol-only draft explicitly states that no supported connections were identified.
It must not be interpreted as evidence that the actual equipment has no connections.

No output establishes safety, complete topology, standards compliance, or design approval.
Engineering decisions remain the responsibility of qualified personnel.

## 9. Download the results

| Link | What you receive |
|---|---|
| **Download drawing + explanation PDF** | New drawing first, followed by reading guidance, source evidence, boundary descriptions, generation mode, and limitations |
| **Download source and generation details** | JSON details opened in the browser, including drawing geometry, source references, version/checksum, and agent information |

You can save the JSON from the browser if needed; you do not have to edit it to use the
application. Save the PDF and its explanation pages together so the drawing's limitations
and provenance remain available.

## 10. Example

Suppose a selected pump connects through a valve to a vessel, with a pressure-instrument
branch and a continuation labeled `TO DWG 2`.

On supported source geometry, the new sheet includes the pump, connected valve, vessel,
instrument branch, and connecting linework. A B marker identifies the continuation.
An unrelated line crossing the pipeline without a recognized junction is not automatically
included, and equipment on drawing/page 2 is not followed.

This example explains the scope; it is not a preloaded drawing or a guarantee that every
source symbol convention will be recognized.

## 11. Troubleshooting

| What you see | What to check or do |
|---|---|
| Upload rejected | Choose a real `.pdf` within 32 MiB and 50 pages. Renaming an image does not make it a supported PDF |
| Equipment/Generate controls disabled | Upload must finish, an equipment item must be selected, and any active drag/processing must finish |
| No detected labels | Select directly on the drawing; automatic label indexing is incomplete |
| Clicking Generate starts selection | The chosen label does not yet have an equipment-symbol outline; draw it, then click Generate again |
| Selection too wide | Select the central symbol/package, not the entire system; tracing expands the context automatically |
| Only the selected symbol appears | No supported connections were recovered; check the stated coverage limits and source vector content |
| Some supporting items are missing | Geometry may contain gaps, curves, open/unrecognized symbols, or disconnected annotations; absence is not a finding about the plant |
| Unrelated items appear connected | Geometric contacts or symbol grouping may be ambiguous; do not treat the inferred topology as independently verified |
| Grouped forms, rotation, or source-origin error | The PDF organization is unsupported. Obtain a compatible export through your approved drawing process; no crop fallback is used |
| Limit exceeded | The page/system exceeds a documented processing/output limit; it has not been silently truncated into a complete result |
| Preview looks small | Download the vector PDF and zoom in; the sheet may have expanded to retain original symbol scale |
| Document-version conflict | The document changed during the operation. Upload again and select the intended equipment in the current session |
| Azure agents fail | Check authorization, endpoint/deployment, credentials, private connectivity, quota, and structured-output support with your administrator |
| Unexpected operation/preview error | Check the terminal running the server for details; do not interpret a failed operation as a valid empty result |
| Application still looks like an older version | Restart the server and refresh the browser |

If an agent-assisted attempt fails, it does not silently become local success. To make
a separate local attempt, uncheck the agent option and generate again.

## 12. Storage, privacy, and ending a session

- Uploaded PDFs and document/generation records persist locally under the application
  data directory. Refreshing or closing the browser does **not** delete them.
- The browser starts with an upload screen and does not offer a stored-document browser.
  Re-upload the file to start a new browser session.
- There is no in-app delete/retention manager. Follow your organization's approved
  handling and retention process; do not assume closing the app purges confidential data.
- Sources, downloaded PDFs, and JSON exports can contain confidential engineering data.
- Azure mode transmits extracted source records, which can also be confidential.
- Stop the local server with **Ctrl+C** in its terminal when finished.

This remains a local prototype without production user authentication or document-level
authorization. Do not expose it directly on a public/shared network.

For technical details, see [architecture](./ARCHITECTURE.md).