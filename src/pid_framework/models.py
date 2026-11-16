from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1, max_length=4000, pattern=r"\S")]
Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Location(Contract):
    page: int = Field(ge=1)
    bbox: tuple[Score, Score, Score, Score]

    @model_validator(mode="after")
    def ordered(self) -> Self:
        x1, y1, x2, y2 = self.bbox
        if x1 >= x2 or y1 >= y2:
            raise ValueError("Bounding box must have positive width and height")
        return self


class Evidence(Contract):
    id: Text
    location: Location
    text: Text
    origin: Literal["pdf_text", "engineer_selection"] = "pdf_text"


class Specification(Contract):
    name: Text
    value: Text
    evidence_ids: list[Text] = Field(min_length=1)


class Equipment(Contract):
    id: Text
    tag: Text
    kind: Text = "equipment"
    evidence_ids: list[Text] = Field(min_length=1)
    confirmed: bool = False
    specifications: list[Specification] = Field(default_factory=list)


class Connection(Contract):
    id: Text
    source: Text
    target: Text
    description: Text
    evidence_ids: list[Text] = Field(min_length=1)
    confirmed: bool = False


class EngineeringGraph(Contract):
    equipment: list[Equipment] = Field(default_factory=list, max_length=1000)
    connections: list[Connection] = Field(default_factory=list, max_length=3000)

    @model_validator(mode="after")
    def references(self) -> Self:
        ids = [e.id for e in self.equipment]
        if len(set(ids)) != len(ids):
            raise ValueError("Equipment IDs must be unique")
        edges = [e.id for e in self.connections]
        if len(set(edges)) != len(edges):
            raise ValueError("Connection IDs must be unique")
        for edge in self.connections:
            if edge.source not in ids or edge.target not in ids or edge.source == edge.target:
                raise ValueError("Connections require two distinct existing equipment IDs")
        return self


class Page(Contract):
    number: int
    width: float
    height: float
    text_characters: int


class Document(Contract):
    id: Text
    filename: Text
    revision: Text
    sha256: Text
    pages: list[Page]
    evidence: list[Evidence]
    graph: EngineeringGraph
    version: int = 1
    limitations: list[str]

    @model_validator(mode="after")
    def references(self) -> Self:
        known = {e.id for e in self.evidence}
        if len(known) != len(self.evidence):
            raise ValueError("Evidence IDs must be unique")
        for evidence in self.evidence:
            if evidence.location.page > len(self.pages):
                raise ValueError("Evidence page is outside document")
        for entity in self.graph.equipment:
            refs = entity.evidence_ids + [
                ref for spec in entity.specifications for ref in spec.evidence_ids
            ]
            if set(refs) - known:
                raise ValueError(f"Unknown evidence on {entity.id}")
        for edge in self.graph.connections:
            if set(edge.evidence_ids) - known:
                raise ValueError(f"Unknown evidence on connection {edge.id}")
        return self


class GraphUpdate(Contract):
    expected_version: int = Field(ge=1)
    graph: EngineeringGraph


class Selection(Contract):
    expected_version: int = Field(ge=1)
    tag: Text | None = None
    kind: Text = "equipment"
    location: Location
    equipment_id: Text | None = None


class Finding(Contract):
    rule_id: Text
    entity_ids: list[Text]
    evidence_ids: list[Text] = Field(min_length=1)
    observation: Text
    requested_action: Text
    category: Literal["data_quality", "engineering_query", "potential_technical_issue"]
    priority: Literal["low", "medium", "high"] = "medium"
    status: Literal["pending_engineer_review"] = "pending_engineer_review"


class AgentFindings(Contract):
    findings: list[Finding] = Field(default_factory=list, max_length=100)


class Review(Contract):
    id: Text
    document_id: Text
    document_version: int
    revision: Text
    findings: list[Finding]
    agent_used: bool
    limitations: list[str]


class PackageRequest(Contract):
    equipment_id: Text
    expected_version: int = Field(ge=1)
    include_connected: bool = True
    boundary_ids: list[Text] = Field(default_factory=list)
    include_proposals: bool = True


class Package(Contract):
    id: Text
    document_id: Text
    document_version: int
    revision: Text
    selected_equipment_id: Text
    equipment: list[Equipment]
    connections: list[Connection]
    evidence: list[Evidence]
    missing_information: list[str]
    proposals: list[str]
    status: Literal["draft_not_for_construction"] = "draft_not_for_construction"
    limitations: list[str]


class BoundaryRequest(Contract):
    equipment_id: Text
    expected_version: int = Field(ge=1)


class BoundaryProposal(Contract):
    document_version: int
    region: Location
    connected_components: int
    method: Literal["connected_ink_linework"] = "connected_ink_linework"
    warnings: list[str]


class OriginalExtractRequest(BoundaryRequest):
    regions: list[Location] = Field(min_length=1, max_length=20)
    engineer_confirmed: Literal[True]


class RegionText(Contract):
    location: Location
    text: str


class OriginalExtract(Contract):
    id: Text
    document_id: Text
    document_version: int
    source_sha256: Text
    revision: Text
    equipment_id: Text
    equipment_tag: Text
    regions: list[Location]
    extracted_text: list[RegionText]
    engineer_confirmed: Literal[True] = True
    status: Literal["source_extract_not_design_approval"] = "source_extract_not_design_approval"
    warnings: list[str]


class DrawingNode(Contract):
    id: Text
    label: Text
    kind: Text
    region: Location
    position: tuple[Score, Score]
    text_only: bool = False
    source_object_ids: list[int] | None = None


class DrawingConnection(Contract):
    id: Text
    source: Text
    target: Text
    source_port: tuple[Score, Score]
    target_port: tuple[Score, Score]
    source_region: Location
    source_object_id: int = Field(ge=0)
    description: Text
    source_points: tuple[tuple[Score, Score], tuple[Score, Score]] | None = None


class ReconstructionModel(Contract):
    page: int = Field(ge=1)
    nodes: list[DrawingNode] = Field(min_length=1, max_length=500)
    connections: list[DrawingConnection] = Field(default_factory=list, max_length=1000)
    template_regions: list[Location] = Field(default_factory=list, max_length=10)
    warnings: list[str]
    source_size: tuple[float, float] | None = None
    sheet_size: tuple[float, float] | None = None

    def node_size(self, node: DrawingNode) -> tuple[float, float]:
        sx, sy = self.source_size or (1, 1)
        dx, dy = self.sheet_size or (sx, sy)
        return (
            (node.region.bbox[2] - node.region.bbox[0]) * sx / dx,
            (node.region.bbox[3] - node.region.bbox[1]) * sy / dy,
        )

    @model_validator(mode="after")
    def valid_connections(self) -> Self:
        if (self.source_size is None) != (self.sheet_size is None):
            raise ValueError("Source and destination sheet sizes must be specified together")
        for size in (self.source_size, self.sheet_size):
            if size is not None and any(not 1 <= value <= 14400 for value in size):
                raise ValueError("Sheet dimensions must be finite and between 1 and 14400 points")
        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("Drawing node IDs must be unique")
        connection_ids = [edge.id for edge in self.connections]
        if len(set(connection_ids)) != len(connection_ids):
            raise ValueError("Drawing connection IDs must be unique")
        for edge in self.connections:
            if edge.source not in ids or edge.target not in ids or edge.source == edge.target:
                raise ValueError("Drawing connections require two different existing nodes")
        if any(node.region.page != self.page for node in self.nodes):
            raise ValueError("This reconstruction supports source objects from one sheet")
        return self


class ReconstructionPreviewRequest(BoundaryRequest):
    drawing: ReconstructionModel


class ReconstructionRequest(ReconstructionPreviewRequest):
    engineer_confirmed: Literal[True]


class ReconstructedDrawing(Contract):
    id: Text
    document_id: Text
    document_version: int
    source_sha256: Text
    revision: Text
    equipment_id: Text
    drawing: ReconstructionModel
    engineer_confirmed: bool
    status: Literal[
        "reconstructed_draft_not_for_construction",
        "unconfirmed_preview",
        "generated_draft_not_for_construction",
    ]


class GenerationRequest(BoundaryRequest):
    use_agents: bool = False


class EvidenceContext(Contract):
    evidence_ids: list[Text] = Field(min_length=1, max_length=100)


class NodePosition(Contract):
    node_id: Text
    position: tuple[Score, Score]


class DrawingLayout(Contract):
    positions: list[NodePosition] = Field(min_length=1, max_length=500)


class ExplainedEvidence(Contract):
    evidence_id: Text
    explanation: Text


class GenerationExplanation(Contract):
    items: list[ExplainedEvidence] = Field(max_length=100)


class GeneratedDrawing(Contract):
    id: Text
    document_id: Text
    document_version: int
    equipment_tag: Text
    reconstruction: ReconstructedDrawing
    summary: Text
    limitations: list[Text]
    source_evidence: list[Evidence]
    agent_explanation: GenerationExplanation | None = None
    agents_used: list[Text] = Field(default_factory=list)
    model_deployment: str | None = None
