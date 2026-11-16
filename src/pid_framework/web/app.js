"use strict";
const $ = id => document.getElementById(id);
let doc = null, selected = null, zoom = 100, selecting = false, start = null, busy = false;
let outlineTarget = null;

function message(text, error = false) {
  $("message").textContent = text;
  $("message").className = error ? "error" : "";
}
async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  return response.json();
}
function jsonOptions(value) {
  return {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(value)};
}
function controls() {
  $("upload").disabled = busy;
  for (const id of ["page", "zoomIn", "zoomOut", "selectRegion"]) $(id).disabled = busy || !doc;
  $("components").disabled = busy || !doc?.graph.equipment.length;
  $("generate").disabled = busy || !selected || selecting;
  $("agents").disabled = busy;
  $("cancelSelection").hidden = !selecting;
}
function clearOutput() { $("output").hidden = true; $("preview").removeAttribute("src"); }
function cancelSelection() {
  selecting = false; start = null; outlineTarget = null;
  $("overlay").style.pointerEvents = "none";
  $("overlay").style.cursor = "";
  $("overlay").querySelector("[data-drag]")?.remove();
  controls();
}
async function operation(task) {
  if (busy) return;
  busy = true; controls();
  try { await task(); } catch (error) { message(error.message, true); }
  finally { busy = false; controls(); }
}
function box(bounds, color, callback) {
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("x", bounds[0] * 1000); rect.setAttribute("y", bounds[1] * 1000);
  rect.setAttribute("width", (bounds[2] - bounds[0]) * 1000);
  rect.setAttribute("height", (bounds[3] - bounds[1]) * 1000);
  rect.setAttribute("fill", `${color}22`); rect.setAttribute("stroke", color);
  rect.setAttribute("stroke-width", "2");
  rect.onclick = () => { if (!selecting && !busy) callback(); };
  $("overlay").append(rect);
  return rect;
}
function renderPage() {
  if (!doc) return;
  $("pageImage").src = `/documents/${doc.id}/pages/${$("page").value}.png`;
  $("pageImage").hidden = false;
  $("sheet").style.width = `${zoom}%`;
  $("overlay").replaceChildren();
  for (const item of doc.graph.equipment) {
    for (const ref of item.evidence_ids) {
      const evidence = doc.evidence.find(e => e.id === ref);
      if (evidence?.location.page === Number($("page").value)) {
        box(evidence.location.bbox, item.id === selected?.id ? "#da541b" : "#126b99",
          () => choose(item.id));
      }
    }
  }
}
function choose(id) {
  cancelSelection(); clearOutput();
  selected = doc.graph.equipment.find(item => item.id === id) || null;
  $("components").value = id;
  if (selected) {
    const refs = doc.evidence.filter(e => selected.evidence_ids.includes(e.id));
    const region = refs.find(e => e.origin === "engineer_selection");
    $("page").value = (region || refs[0]).location.page;
    $("selected").textContent = `${selected.tag}${region ? " — source outline selected." : " — select its symbol outline on the drawing."}`;
  } else $("selected").textContent = "No equipment selected.";
  renderPage(); controls();
}
function loadDocument(value, equipmentId = "") {
  doc = value; selected = null; cancelSelection(); clearOutput();
  $("filename").textContent = doc.filename;
  $("page").replaceChildren(...doc.pages.map(page => new Option(`Page ${page.number}`, page.number)));
  $("components").replaceChildren(new Option("Choose a label or select on drawing", ""));
  for (const item of doc.graph.equipment) $("components").add(new Option(item.tag, item.id));
  $("original").href = `/documents/${doc.id}/source`; $("original").hidden = false;
  choose(equipmentId);
}
$("upload").onclick = () => operation(async () => {
  const file = $("file").files[0];
  if (!file || !file.name.toLowerCase().endsWith(".pdf")) throw new Error("Choose a P&ID PDF file.");
  if (file.size > 32 * 1024 * 1024) throw new Error("Maximum PDF size is 32 MiB.");
  message("Reading your P&ID PDF…");
  const value = await api(`/documents?filename=${encodeURIComponent(file.name)}`,
    {method: "POST", headers: {"Content-Type": "application/pdf"}, body: file});
  zoom = 100; loadDocument(value);
  message("PDF uploaded. Select equipment directly on the drawing, or choose a source label first.");
});
$("components").onchange = () => choose($("components").value);
$("page").onchange = () => { cancelSelection(); renderPage(); };
$("zoomIn").onclick = () => { cancelSelection(); zoom = Math.min(400, zoom + 25); renderPage(); };
$("zoomOut").onclick = () => { cancelSelection(); zoom = Math.max(50, zoom - 25); renderPage(); };
$("pageImage").onerror = () => message("Unable to display this source page. Check the server logs and try uploading the PDF again.", true);
function beginSelection(equipmentId = null) {
  if (!doc || busy) return;
  outlineTarget = equipmentId;
  if (!equipmentId) {
    selected = null; $("components").value = "";
    $("selected").textContent = "Select the equipment symbol on the drawing.";
    renderPage();
  }
  selecting = true; start = null; clearOutput();
  $("overlay").style.pointerEvents = "all"; $("overlay").style.cursor = "crosshair";
  controls();
  message(`Drag around ${selected ? selected.tag : "the equipment"} symbol/package outline. No tag entry is needed.`);
}
$("selectRegion").onclick = () => beginSelection();
$("cancelSelection").onclick = () => { cancelSelection(); renderPage(); message("Selection cancelled."); };
document.addEventListener("keydown", event => { if (event.key === "Escape") cancelSelection(); });
$("overlay").onpointercancel = cancelSelection;
function point(event) {
  const bounds = $("overlay").getBoundingClientRect();
  return [Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
    Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height))];
}
$("overlay").onpointerdown = event => {
  if (!selecting || busy) return;
  event.preventDefault(); start = point(event); $("overlay").setPointerCapture(event.pointerId);
};
function dragBox(end) {
  return [Math.min(start[0], end[0]), Math.min(start[1], end[1]),
    Math.max(start[0], end[0]), Math.max(start[1], end[1])];
}
$("overlay").onpointermove = event => {
  if (!selecting || !start) return;
  $("overlay").querySelector("[data-drag]")?.remove();
  box(dragBox(point(event)), "#da541b", () => {}).setAttribute("data-drag", "true");
};
$("overlay").onpointerup = event => {
  if (!selecting || !start) return;
  const bbox = dragBox(point(event)), equipmentId = outlineTarget;
  cancelSelection();
  operation(async () => {
    if (bbox[0] === bbox[2] || bbox[1] === bbox[3]) throw new Error("Drag a symbol region with positive width and height.");
    const value = await api(`/documents/${doc.id}/selections`, jsonOptions({
      expected_version: doc.version,
      ...(equipmentId ? {equipment_id: equipmentId} : {}),
      location: {page: Number($("page").value), bbox}
    }));
    loadDocument(value, equipmentId || value.graph.equipment.at(-1).id);
    message("Equipment selected. Generate its new equipment P&ID.");
  });
};
function list(id, values) {
  $(id).replaceChildren(...values.map(text => {
    const item = document.createElement("li"); item.textContent = text; return item;
  }));
}
$("generate").onclick = () => {
  if (!selected) return;
  const outlined = doc.evidence.some(e => selected.evidence_ids.includes(e.id) && e.origin === "engineer_selection");
  if (!outlined) { beginSelection(selected.id); return; }
  operation(async () => {
    clearOutput();
    message("Generating a new source-supported equipment drawing…");
    const result = await api(`/documents/${doc.id}/generations`, jsonOptions({
      equipment_id: selected.id, expected_version: doc.version, use_agents: $("agents").checked
    }));
    $("summary").textContent = result.summary;
    $("generationMode").textContent = result.agents_used.length
      ? `Microsoft Agent Framework: ${result.agents_used.join(" → ")}. Deployment: ${result.model_deployment}.`
      : "Generated locally. No AI agents or model calls were used.";
    $("downloadPdf").href = `/generations/${result.id}/drawing.pdf`;
    $("downloadJson").href = `/generations/${result.id}`;
    $("preview").src = `/generations/${result.id}/preview.png`;
    list("evidence", result.source_evidence.map(item => `Page ${item.location.page} · ${item.text}`));
    $("aiExplanation").hidden = !result.agent_explanation?.items.length;
    list("explanations", (result.agent_explanation?.items || []).map(item => `${item.explanation} [${item.evidence_id}]`));
    list("limitations", result.limitations);
    const boundaries = result.reconstruction.drawing.nodes
      .filter(node => node.kind === "continuation_boundary").map(node => node.label);
    $("boundariesHeading").hidden = !boundaries.length;
    list("systemBoundaries", boundaries);
    $("output").hidden = false; $("output").scrollIntoView({block: "start"});
    message("New equipment P&ID draft generated. The download includes source information and coverage limitations.");
  });
};
$("preview").onerror = () => message("The drawing preview could not be loaded. Check server logs before relying on the output.", true);
$("agents").onchange = () => {
  clearOutput();
  $("mode").textContent = $("agents").checked
    ? "Optional evidence → layout → explanation agents. Extracted source records are sent to Azure."
    : "Local generation. No AI model calls.";
};
api("/health").then(health => {
  $("agentsLabel").hidden = !health.agents_configured;
}).catch(error => message(`Server configuration unavailable: ${error.message}`, true));
controls();
