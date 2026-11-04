"""Serializable import results; paths are relative to the uploaded archive."""

from dataclasses import dataclass, field


@dataclass
class ProjectFile:
    path: str
    kind: str
    size_bytes: int
    status: str = "discovered_not_parsed"


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    evidence: list[str] = field(default_factory=list)
    source: str = "checker"
    component: str | None = None
    confidence: float = 1.0
    suggested_fix: str | None = None
    excluded: bool = False
    items: list[dict] = field(default_factory=list)
    details: dict = field(default_factory=dict)


@dataclass
class Pin:
    number: str
    name: str = ""
    electrical_type: str = ""
    net: str = ""
    no_connect: bool = False


@dataclass
class Pad:
    number: str
    net: str
    line: int
    kind: str = ""
    name: str = ""
    # Measured footprint geometry in mm, relative to the footprint origin.
    # None when the pad node carries no usable at/size (e.g. custom shapes).
    x_mm: float | None = None
    y_mm: float | None = None
    width_mm: float | None = None
    height_mm: float | None = None


@dataclass
class ComponentRecord:
    """One symbol unit or PCB footprint, retaining its original source fields."""

    reference: str
    value: str
    footprint: str
    source_file: str
    line: int
    uuid: str = ""
    symbol: str = ""
    unit: int = 1
    in_bom: bool = True
    on_board: bool = True
    dnp: bool = False
    board_only: bool = False
    attributes: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    pads: list[Pad] = field(default_factory=list)
    x_mm: float | None = None
    y_mm: float | None = None
    rotation: float | None = None
    layer: str = ""

    @property
    def location(self) -> str:
        return f"{self.source_file}:{self.line}"


@dataclass
class Component:
    reference: str
    schematic: list[ComponentRecord] = field(default_factory=list)
    pcb: list[ComponentRecord] = field(default_factory=list)
    schematic_pins: list[Pin] = field(default_factory=list)
    bom: list[dict] = field(default_factory=list)
    cpl: list[dict] = field(default_factory=list)


@dataclass
class ParsedComponents:
    components: list[ComponentRecord] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    comparison_supported: bool = True
    non_physical_symbols: int = 0
    document_uuid: str = ""


@dataclass
class Project:
    archive: str
    files: list[ProjectFile] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    stage: str = "file_discovery_only"
    components: dict[str, Component] = field(default_factory=dict)
    selected_files: dict[str, str] = field(default_factory=dict)
    checks_performed: list[str] = field(default_factory=list)
    non_physical_symbols: int = 0
    schematic_nets: dict[str, list[str]] = field(default_factory=dict)
    pcb_nets: dict[str, list[str]] = field(default_factory=dict)
    connectivity: dict[str, int | str] = field(default_factory=dict)
    engine: dict[str, str] = field(default_factory=dict)
    native_reports: dict[str, dict] = field(default_factory=dict)
    circuit: dict = field(default_factory=dict)
    review: dict = field(default_factory=dict)
    ai_review: dict = field(default_factory=lambda: {"status": "not_requested"})
    assembly: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    design_context: dict = field(default_factory=dict)
    datasheets: dict = field(default_factory=dict)
    action_plan: dict = field(default_factory=dict)
    assembly_export: dict = field(default_factory=dict)
    signal_paths: dict = field(default_factory=dict)
    manufacturing: dict = field(default_factory=dict)
    board_checks: dict = field(default_factory=dict)
    fabrication: dict = field(default_factory=dict)
    verification: dict = field(default_factory=dict)
