"""Recognize files conservatively without claiming to parse their contents."""

import csv
import io
from pathlib import PurePosixPath


def identify_file(path: str, sample: bytes) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    known = {
        ".kicad_pro": "kicad_project", ".kicad_dru": "kicad_rules", ".kicad_sch": "schematic",
        ".kicad_pcb": "pcb", ".gbr": "gerber", ".ger": "gerber",
        ".gtl": "gerber", ".gbl": "gerber", ".gts": "gerber",
        ".gbs": "gerber", ".gto": "gerber", ".gbo": "gerber",
        ".gm1": "gerber", ".gko": "gerber", ".drl": "drill",
        ".gbrjob": "gerber_job", ".pdf": "supporting_document",
    }
    filename = PurePosixPath(path).name.lower()
    if filename in ("fabricationtoolkit.json", "fabrication-toolkit-options.json"):
        return "fabrication_settings"
    if suffix == ".kicad_prl":
        return "kicad_preferences"
    if suffix in known:
        return known[suffix]
    if suffix != ".csv":
        return "other"
    try:
        text = sample.decode("utf-8-sig")
        dialect = csv.Sniffer().sniff(text, delimiters=",;\t")
        headers = next(csv.reader(io.StringIO(text), dialect))
    except (UnicodeError, csv.Error, StopIteration):
        return "csv_unknown"
    fields = {"".join(c for c in header.lower() if c.isalnum()) for header in headers}
    references = {"reference", "references", "ref", "refs", "refdes", "designator", "designators"}
    if not fields & references:
        return "csv_unknown"
    x_fields = {"x", "posx", "midx", "centerx", "xmm", "posxmm", "midxmm", "centerxmm"}
    y_fields = {"y", "posy", "midy", "centery", "ymm", "posymm", "midymm", "centerymm"}
    if (fields & (x_fields | {"xin", "posxin", "midxin"}) or fields & (y_fields | {"yin", "posyin", "midyin"})
            or fields & {"rotation", "rot", "angle"} and fields & {"boardside", "side", "layer"}):
        return "cpl_candidate"
    if fields & {"value", "comment", "description", "mpn", "manufacturerpartnumber", "lcscpartnumber", "footprint", "package", "quantity", "qty"}:
        return "bom_candidate"
    return "csv_unknown"
