"use strict";
const $ = id => document.getElementById(id);
let doc = null, selected = null, zoom = 100, selecting = false, start = null, reviewResult = null;
let cropRegions = [];
let reconstructionRegions = [];
let previewUrl = null;
let outlineEquipmentId = null;
function resetReconstruction() {
  reconstructionRegions = [];
  $("reconstructionModel").value = "{}";
  $("confirmReconstruction").checked = false;
  $("reconstructionWarnings").textContent = "";
  $("reconstructionExports").replaceChildren();
  $("drawingNodeControls").replaceChildren();
  $("reconstructionPreview").hidden = true;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
}
function resetCrops() {
  cropRegions = [];
  $("cropRegions").value = "[]";
  $("confirmCrops").checked = false;
  $("traceWarnings").textContent = "";
  $("originalExports").replaceChildren();
}
function message(text, error = false) {
  $("message").textContent = text; $("message").className = error ? "error" : "";
}
async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status}: ${body}`);
  }
  return response.json();
}
function action(id, operation) {
  $(id).onclick = async () => {
    $(id).disabled = true;
    try { await operation(); } catch (error) { message(error.message, true); }
    finally { $(id).disabled = false; }
  };
}
function jsonOptions(method, value) {
  return {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(value)};
}
async function refreshDocuments() {
  const documents = await api("/documents");
  $("documents").replaceChildren(new Option("Choose document", ""));
  for (const item of documents)
    $("documents").add(new Option(`${item.filename} [${item.revision}]`, item.id));
}
function loadDocument(value) {
  doc = value; selected = null; reviewResult = null;
  resetCrops(); selecting = false; start = null;
  outlineEquipmentId = null;
  resetReconstruction();
  $("overlay").style.pointerEvents = "none";
  $("overlay").style.cursor = "";
  $("cancelSelection").hidden = true;
  $("selectRegion").disabled = false;
  $("documents").value = doc.id;
  $("page").replaceChildren(...doc.pages.map(p => new Option(`Page ${p.number}`, p.number)));
  $("graph").value = JSON.stringify(doc.graph, null, 2);
  $("limitations").textContent = doc.limitations.join("\n\n");
  $("original").href = `/documents/${doc.id}/source`;
  $("documentJson").href = `/documents/${doc.id}`;
  $("findings").replaceChildren(); $("exports").replaceChildren();
  $("selectedTitle").textContent = "Select equipment"; $("selectedInfo").textContent = "";
  $("specifications").value = "[]";
  renderEquipment(); renderPage();
  message(`Loaded ${doc.filename}: ${doc.graph.equipment.length} unverified/confirmed candidates. Version ${doc.version}.`);
}
function renderEquipment() {
  $("equipmentList").replaceChildren();
  $("sourceComponents").replaceChildren(new Option("Choose a source component", ""));
  $("sourceComponents").disabled = !doc || doc.graph.equipment.length === 0;
  $("componentIndexStatus").textContent = doc?.graph.equipment.length
    ? `${doc.graph.equipment.length} indexed source candidates. Labels may be off-sheet references; define the depicted symbol outline before reconstruction. This index is not complete symbol recognition.`
    : "No equipment labels were indexed on this sheet. Select the component directly on the drawing; it will then appear here. Automatic recognition of all symbols is not implemented.";
  for (const equipment of doc?.graph.equipment || []) {
    $("sourceComponents").add(new Option(equipment.tag, equipment.id));
    if (!equipment.tag.toLowerCase().includes($("search").value.toLowerCase())) continue;
    const button = document.createElement("button");
    button.className = "item";
    button.textContent = `${equipment.tag} · ${equipment.confirmed ? "confirmed" : "unverified reference"}`;
    button.onclick = () => selectEquipment(equipment.id);
    $("equipmentList").append(button);
  }
  if (selected) $("sourceComponents").value = selected.id;
}
$("sourceComponents").onchange = () => {
  if ($("sourceComponents").value) selectEquipment($("sourceComponents").value);
};
function selectEquipment(id) {
  resetCrops();
  resetReconstruction();
  selected = doc.graph.equipment.find(e => e.id === id);
  $("sourceComponents").value = id;
  const evidence = doc.evidence.filter(e => selected.evidence_ids.includes(e.id));
  $("selectedTitle").textContent = selected.tag;
  $("selectedInfo").textContent = `${selected.id} · ${selected.confirmed ? "Confirmed" : "Unverified"} · evidence: ${evidence.map(e => e.id).join(", ")}`;
  $("specifications").value = JSON.stringify(selected.specifications, null, 2);
  if (evidence[0]) $("page").value = evidence[0].location.page;
  renderPage();
}
function renderPage() {
  if (!doc) return;
  const page = Number($("page").value);
  $("sourceObjects").href = `/documents/${doc.id}/source-objects?page=${page}`;
  $("pageImage").src = `/documents/${doc.id}/pages/${page}.png`;
  $("sheet").style.width = `${zoom}%`;
  $("overlay").replaceChildren();
  for (const equipment of doc.graph.equipment) {
    for (const ref of equipment.evidence_ids) {
      const evidence = doc.evidence.find(e => e.id === ref);
      if (!evidence || evidence.location.page !== page) continue;
      addBox(evidence.location.bbox, equipment.id === selected?.id ? "#da541b" : "#126b99",
        () => selectEquipment(equipment.id));
    }
  }
  for (const region of cropRegions) {
    if (region.page === page) addBox(region.bbox, "#f07814", () => {});
  }
  for (const region of reconstructionRegions) {
    if (region.page === page) addBox(region.bbox, "#7c3aed", () => {});
  }
}
function addBox(box, color, callback) {
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("x", box[0] * 1000); rect.setAttribute("y", box[1] * 1000);
  rect.setAttribute("width", (box[2] - box[0]) * 1000);
  rect.setAttribute("height", (box[3] - box[1]) * 1000);
  rect.setAttribute("fill", `${color}22`); rect.setAttribute("stroke", color);
  rect.setAttribute("stroke-width", "2");
  rect.onclick = () => { if (!selecting) callback(); }; $("overlay").append(rect);
  return rect;
}
async function saveGraph(graph) {
  const id = selected?.id;
  const updated = await api(`/documents/${doc.id}/graph`, jsonOptions("PUT", {
    expected_version: doc.version, graph
  }));
  loadDocument(updated);
  if (id && doc.graph.equipment.some(e => e.id === id)) selectEquipment(id);
  message(`Graph saved as version ${doc.version}. Prior reviews and packages remain immutable snapshots.`);
}
function requireSelection() {
  if (!doc || !selected) throw new Error("Select an equipment item first.");
}
function reconstructionRequest(confirmed) {
  requireSelection();
  return {
    equipment_id: selected.id, expected_version: doc.version,
    drawing: JSON.parse($("reconstructionModel").value),
    ...(confirmed ? {engineer_confirmed: true} : {})
  };
}
action("proposeReconstruction", async () => {
  requireSelection();
  if (!doc.evidence.some(e => selected.evidence_ids.includes(e.id) && e.origin === "engineer_selection")) {
    beginEquipmentSelection(selected.id);
    message(`Define the actual symbol/package outline for ${selected.tag} on the drawing. The highlighted label alone is not a symbol. Then click Propose again.`);
    return;
  }
  const model = await api(`/documents/${doc.id}/reconstruction-proposals`, jsonOptions("POST", {
    equipment_id: selected.id, expected_version: doc.version
  }));
  resetReconstruction();
  $("reconstructionModel").value = JSON.stringify(model, null, 2);
  $("reconstructionWarnings").textContent = model.warnings.join("\n\n");
  reconstructionRegions = model.nodes.map(n => n.region);
  renderDrawingNodeControls();
  renderPage();
  message(`Proposed ${model.nodes.length} source elements and ${model.connections.length} connections. This is not a complete or confirmed interpretation: correct it before generating.`);
});
function updateDrawingModel(model) {
  $("reconstructionModel").value = JSON.stringify(model, null, 2);
  $("confirmReconstruction").checked = false;
  $("reconstructionExports").replaceChildren();
  $("reconstructionPreview").hidden = true;
  reconstructionRegions = model.nodes.map(node => node.region);
  renderDrawingNodeControls();
  renderPage();
}
function renderDrawingNodeControls() {
  $("drawingNodeControls").replaceChildren();
  const model = JSON.parse($("reconstructionModel").value);
  for (const node of model.nodes || []) {
    const row = document.createElement("div"); row.className = "card";
    const title = document.createElement("strong"); title.textContent = `${node.id}: ${node.label}`;
    row.append(title);
    for (const [axis, index] of [["X", 0], ["Y", 1]]) {
      const label = document.createElement("label"); label.textContent = `New sheet ${axis} `;
      const input = document.createElement("input"); input.type = "number";
      input.min = "0"; input.max = "1"; input.step = "0.01"; input.value = node.position[index];
      input.onchange = () => {
        try {
          const value = Number(input.value);
          if (!Number.isFinite(value) || value < 0 || value > 1) throw new Error("Position must be 0–1.");
          const edited = JSON.parse($("reconstructionModel").value);
          edited.nodes.find(item => item.id === node.id).position[index] = value;
          updateDrawingModel(edited);
        } catch (error) { message(error.message, true); }
      };
      label.append(input); row.append(label);
    }
    const remove = document.createElement("button"); remove.textContent = "Remove element";
    remove.onclick = () => {
      try {
        const edited = JSON.parse($("reconstructionModel").value);
        edited.nodes = edited.nodes.filter(item => item.id !== node.id);
        const before = edited.connections.length;
        edited.connections = edited.connections.filter(edge => edge.source !== node.id && edge.target !== node.id);
        updateDrawingModel(edited);
        message(`Removed ${node.id} and ${before - edited.connections.length} attached connections.`);
      } catch (error) { message(error.message, true); }
    };
    row.append(remove); $("drawingNodeControls").append(row);
  }
}
function beginDrawingSelection(mode) {
  requireSelection();
  const model = JSON.parse($("reconstructionModel").value);
  if (!model.nodes?.length) throw new Error("Propose or author a reconstruction model first.");
  selecting = mode;
  $("confirmReconstruction").checked = false;
  $("overlay").style.pointerEvents = "all"; $("overlay").style.cursor = "crosshair";
  $("viewport").scrollIntoView({block: "center"});
  message("Drag tightly around the missing source symbol or lettering. It will become an independently movable element. Correct its connections in the drawing model.");
}
action("addDrawingSymbol", async () => beginDrawingSelection("drawing-symbol"));
action("addDrawingText", async () => beginDrawingSelection("drawing-text"));
$("reconstructionModel").oninput = () => {
  $("confirmReconstruction").checked = false;
  $("reconstructionExports").replaceChildren();
  $("reconstructionPreview").hidden = true;
};
action("showReconstructionRegions", async () => {
  const model = reconstructionRequest(false).drawing;
  if (!Array.isArray(model.nodes)) throw new Error("Provide a drawing model with nodes.");
  reconstructionRegions = model.nodes.map(node => node.region);
  renderDrawingNodeControls();
  renderPage();
  $("viewport").scrollIntoView({block: "center"});
});
action("previewReconstruction", async () => {
  requireSelection();
  const response = await fetch(`/documents/${doc.id}/reconstruction-preview`, jsonOptions(
    "POST", reconstructionRequest(false)
  ));
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(await response.blob());
  $("reconstructionPreview").src = previewUrl;
  $("reconstructionPreview").hidden = false;
  message("Preview only: inspect the newly laid-out drawing and correct missing or wrong elements before confirmation.");
});
action("generateReconstruction", async () => {
  requireSelection();
  if (selecting) throw new Error("Finish the source selection before generating.");
  if (!$("confirmReconstruction").checked) throw new Error("Correct and verify the reconstruction before generating.");
  const result = await api(`/documents/${doc.id}/reconstructions`, jsonOptions(
    "POST", reconstructionRequest(true)
  ));
  $("reconstructionExports").replaceChildren();
  for (const [label, url] of [
    ["New reconstructed P&ID PDF", `/reconstructions/${result.id}/drawing.pdf`],
    ["Corrected drawing model and provenance", `/reconstructions/${result.id}`]
  ]) {
    const link = document.createElement("a");
    link.textContent = label; link.href = url; link.target = "_blank"; link.rel = "noopener";
    $("reconstructionExports").append(link);
  }
  message("New sheet composed from original vector objects and rerouted connections. Draft only; engineering completeness depends on your corrected model.");
});
action("upload", async () => {
  const file = $("file").files[0];
  if (!file) throw new Error("Choose a PDF file.");
  message("Extracting local PDF text and tag candidates...");
  const value = await api(`/documents?filename=${encodeURIComponent(file.name)}&revision=${encodeURIComponent($("revision").value)}`,
    {method: "POST", headers: {"Content-Type": "application/pdf"}, body: file});
  await refreshDocuments(); loadDocument(value);
});
$("documents").onchange = async () => {
  if (!$("documents").value) return;
  try { loadDocument(await api(`/documents/${$("documents").value}`)); }
  catch (error) { message(error.message, true); }
};
$("search").oninput = renderEquipment;
$("page").onchange = renderPage;
$("zoomIn").onclick = () => { zoom = Math.min(500, zoom + 25); renderPage(); };
$("zoomOut").onclick = () => { zoom = Math.max(50, zoom - 25); renderPage(); };
action("confirm", async () => {
  requireSelection();
  const graph = structuredClone(doc.graph);
  graph.equipment.find(e => e.id === selected.id).confirmed = true;
  await saveGraph(graph);
});
action("saveSpecs", async () => {
  requireSelection();
  const graph = structuredClone(doc.graph);
  graph.equipment.find(e => e.id === selected.id).specifications = JSON.parse($("specifications").value);
  await saveGraph(graph);
});
action("saveGraph", async () => {
  if (!doc) throw new Error("Load a document first.");
  await saveGraph(JSON.parse($("graph").value));
});
action("review", async () => {
  if (!doc) throw new Error("Load a document first.");
  message("Running evidence-linked review...");
  reviewResult = await api(`/documents/${doc.id}/reviews?use_agent=${$("azure").checked}`, {method: "POST"});
  $("findings").replaceChildren();
  for (const finding of reviewResult.findings) {
    const card = document.createElement("div"); card.className = "card";
    const title = document.createElement("strong");
    title.textContent = `${finding.rule_id} · ${finding.category} · ${finding.priority}`;
    card.append(title);
    for (const text of [finding.observation, finding.requested_action]) {
      const p = document.createElement("p"); p.textContent = text; card.append(p);
    }
    for (const ref of finding.evidence_ids) {
      const evidence = doc.evidence.find(e => e.id === ref);
      const button = document.createElement("button");
      button.textContent = `${ref} / page ${evidence.location.page}`;
      button.onclick = () => {
        $("page").value = evidence.location.page; renderPage();
        addBox(evidence.location.bbox, "#dc2626", () => {});
      };
      card.append(button);
    }
    $("findings").append(card);
  }
  const limits = document.createElement("p");
  limits.textContent = reviewResult.limitations.join(" ");
  $("findings").append(limits);
  message(`Saved review ${reviewResult.id}: ${reviewResult.findings.length} review queries/findings. No findings is not a correctness certificate.`);
});
action("downloadReview", async () => {
  if (!reviewResult) throw new Error("Run a review first.");
  const blob = new Blob([JSON.stringify(reviewResult, null, 2)], {type: "application/json"});
  const link = document.createElement("a"); link.href = URL.createObjectURL(blob);
  link.download = `review-${reviewResult.id}.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
});
action("generate", async () => {
  requireSelection();
  const value = await api(`/documents/${doc.id}/packages`, jsonOptions("POST", {
    equipment_id: selected.id, expected_version: doc.version,
    include_connected: $("connected").checked,
    boundary_ids: $("boundaries").value.split(",").map(x => x.trim()).filter(Boolean),
    include_proposals: $("proposals").checked
  }));
  $("exports").replaceChildren();
  for (const [label, url] of [
    ["SVG draft", `/packages/${value.id}/drawing.svg`],
    ["PDF draft", `/packages/${value.id}/drawing.pdf`],
    ["Package JSON", `/packages/${value.id}`]
  ]) {
    const link = document.createElement("a"); link.textContent = label;
    link.href = url; link.target = "_blank"; link.rel = "noopener";
    $("exports").append(link);
  }
  message("Draft package saved. Missing information and proposed review tasks are explicit; no unverified piping has been added.");
});
action("selectRegion", async () => {
  beginEquipmentSelection(null);
});
function beginEquipmentSelection(equipmentId) {
  if (!doc) throw new Error("Import or open a drawing first.");
  outlineEquipmentId = equipmentId;
  selecting = "equipment";
  $("overlay").style.pointerEvents = "all";
  $("overlay").style.cursor = "crosshair";
  $("cancelSelection").hidden = false;
  $("viewport").scrollIntoView({block: "center"});
  message("Drag around the equipment symbol/package on the original P&ID. No tag entry is needed. Include the label if available.");
}
function cancelSelection() {
  selecting = false; start = null;
  outlineEquipmentId = null;
  $("overlay").style.pointerEvents = "none"; $("overlay").style.cursor = "";
  $("overlay").querySelector("[data-drag]")?.remove();
  $("cancelSelection").hidden = true;
}
$("cancelSelection").onclick = () => {
  cancelSelection(); message("Selection cancelled. The drawing remains available for navigation.");
};
$("overlay").onpointercancel = cancelSelection;
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && selecting) cancelSelection();
});
function beginCropSelection(mode) {
  requireSelection();
  $("confirmCrops").checked = false;
  $("originalExports").replaceChildren();
  selecting = mode;
  $("overlay").style.pointerEvents = "all";
  $("overlay").style.cursor = "crosshair";
  $("viewport").scrollIntoView({block: "center"});
  message(mode === "crop" ? "Drag the replacement equipment crop boundary." :
    "Drag a source notes/specification region. It will become an additional PDF page.");
}
function setCrops(regions) {
  cropRegions = regions;
  $("cropRegions").value = JSON.stringify(regions, null, 2);
  $("confirmCrops").checked = false;
  $("originalExports").replaceChildren();
  renderPage();
}
action("traceBoundary", async () => {
  requireSelection();
  const proposal = await api(`/documents/${doc.id}/boundary-proposals`, jsonOptions("POST", {
    equipment_id: selected.id, expected_version: doc.version
  }));
  setCrops([proposal.region]);
  $("page").value = proposal.region.page;
  $("traceWarnings").textContent = proposal.warnings.join("\n\n");
  renderPage();
  $("viewport").scrollIntoView({block: "center"});
  message("Boundary proposed from connected linework. Inspect crossings, instruments, detached notes, and off-page references before confirming.");
});
action("adjustCrop", async () => beginCropSelection("crop"));
action("addNoteCrop", async () => beginCropSelection("note"));
$("cropRegions").oninput = () => {
  $("confirmCrops").checked = false; $("originalExports").replaceChildren();
};
action("previewCrops", async () => {
  requireSelection();
  const regions = JSON.parse($("cropRegions").value);
  if (!Array.isArray(regions) || !regions.length) throw new Error("Provide at least one crop region.");
  for (const region of regions) {
    if (!Number.isInteger(region.page) || region.page < 1 || region.page > doc.pages.length ||
        !Array.isArray(region.bbox) || region.bbox.length !== 4 ||
        region.bbox.some(n => !Number.isFinite(n) || n < 0 || n > 1) ||
        region.bbox[0] >= region.bbox[2] || region.bbox[1] >= region.bbox[3]) {
      throw new Error("Invalid page or bounding box. Use normalized [left, top, right, bottom].");
    }
  }
  setCrops(regions);
});
action("exportOriginal", async () => {
  requireSelection();
  if (selecting) throw new Error("Finish drawing the source region before exporting.");
  if (!$("confirmCrops").checked) throw new Error("Inspect and confirm the crop boundaries and source notes first.");
  const result = await api(`/documents/${doc.id}/original-extracts`, jsonOptions("POST", {
    equipment_id: selected.id, expected_version: doc.version,
    regions: JSON.parse($("cropRegions").value), engineer_confirmed: true
  }));
  $("originalExports").replaceChildren();
  for (const [label, url] of [
    ["Original-fidelity PDF", `/original-extracts/${result.id}/drawing.pdf`],
    ["Source text and provenance JSON", `/original-extracts/${result.id}`]
  ]) {
    const link = document.createElement("a");
    link.textContent = label; link.href = url; link.target = "_blank"; link.rel = "noopener";
    $("originalExports").append(link);
  }
  message("Source P&ID extract saved. Original PDF graphics are retained, not redrawn. Only confirmed crop regions are included; completeness is engineer-verified.");
});
function point(event) {
  const bounds = $("overlay").getBoundingClientRect();
  return [Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
    Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height))];
}
$("overlay").onpointerdown = event => {
  if (!selecting) return;
  event.preventDefault(); start = point(event); $("overlay").setPointerCapture(event.pointerId);
};
$("overlay").onpointermove = event => {
  if (!selecting || !start) return;
  const end = point(event);
  $("overlay").querySelector("[data-drag]")?.remove();
  const rect = addBox([Math.min(start[0], end[0]), Math.min(start[1], end[1]),
    Math.max(start[0], end[0]), Math.max(start[1], end[1])], "#f07814", () => {});
  rect.setAttribute("data-drag", "true");
};
$("overlay").onpointerup = async event => {
  if (!selecting || !start) return;
  const end = point(event);
  const bbox = [Math.min(start[0], end[0]), Math.min(start[1], end[1]),
    Math.max(start[0], end[0]), Math.max(start[1], end[1])];
  const mode = selecting;
  selecting = false; start = null; $("overlay").style.pointerEvents = "none";
  $("overlay").style.cursor = "";
  $("cancelSelection").hidden = true;
  try {
    if (bbox[0] === bbox[2] || bbox[1] === bbox[3]) throw new Error("Drag a region with positive width and height.");
    if (mode === "drawing-symbol" || mode === "drawing-text") {
      const model = JSON.parse($("reconstructionModel").value);
      const id = `added-${crypto.randomUUID()}`;
      const main = model.nodes[0];
      model.nodes.push({
        id, label: mode === "drawing-text" ? "Selected source annotation" : "Selected source symbol",
        kind: mode === "drawing-text" ? "annotation" : $("manualKind").value,
        region: {page: Number($("page").value), bbox},
        position: [
          Math.max(0.03, Math.min(0.75 - (bbox[2] - bbox[0]), main.position[0] + bbox[0] - main.region.bbox[0])),
          Math.max(0.08, Math.min(0.9 - (bbox[3] - bbox[1]), main.position[1] + bbox[1] - main.region.bbox[1]))
        ],
        text_only: mode === "drawing-text"
      });
      updateDrawingModel(model);
      message(`Added ${id}. Adjust its new-sheet position and add/verify its connections in the model before previewing.`);
      return;
    }
    if (mode === "crop" || mode === "note") {
      const region = {page: Number($("page").value), bbox};
      const existing = JSON.parse($("cropRegions").value);
      setCrops(mode === "crop" ? [region, ...existing.slice(1)] : [...existing, region]);
      message("Crop updated. Inspect all regions, then confirm the source information is complete.");
      return;
    }
    const equipmentId = outlineEquipmentId;
    outlineEquipmentId = null;
    const value = await api(`/documents/${doc.id}/selections`, jsonOptions("POST", {
      expected_version: doc.version, kind: $("manualKind").value || "equipment",
      ...(equipmentId ? {equipment_id: equipmentId} : {}),
      location: {page: Number($("page").value), bbox}
    }));
    loadDocument(value); selectEquipment(equipmentId || value.graph.equipment.at(-1).id);
    message("Component outline saved. Click Propose equipment reconstruction, inspect the preview, and correct the model before generating.");
  } catch (error) { message(error.message, true); }
};
refreshDocuments().catch(error => message(error.message, true));
