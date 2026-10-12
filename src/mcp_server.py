"""Read-only local MCP bridge. Install requirements-mcp.txt, then run as a module.

The launching operator chooses --data-dir. Tool arguments cannot select files,
execute code, change the calculation, or send messages. No model is bundled.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
from pathlib import Path
import site
from typing import Any, Literal

from examples.research_brief.reproduce import calculate, load_rows

SOURCES = {"D006": "d006/2024/rio_2024.csv", "D008": "d008/2024/kontr_k.csv"}
OPTIONS = {"year": 2024, "grouping": "household_size_1_2_3_4_5_plus", "aggregation": "median"}
TOOL_NAMES = {"describe_housing_dataset", "preview_housing_brief", "calculate_housing_brief"}
LIMITATIONS = [
    "Only the synthetic 2024 D006/D008 housing example is supported; no population estimates.",
    "An external MCP client chooses its model; this server has no AI planner or model connection.",
    "Only aggregate results leave this server, but small groups may still disclose values. Disclosure control is not implemented.",
    "This is a local prototype, not a security-reviewed government deployment.",
]


def validate_options(year=2024, grouping=OPTIONS["grouping"], aggregation="median"):
    if type(year) is not int or year != 2024 or grouping != OPTIONS["grouping"] or aggregation != "median":
        raise ValueError("Unsupported options: use year 2024, the fixed household-size groups, and median.")
    return dict(OPTIONS)


class HousingBridge:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()

    def describe(self) -> dict[str, Any]:
        return {"dataset": "synthetic_housing_2024", "year": 2024,
            "sources": SOURCES.copy(), "analysis_unit": "household", "synthetic_data": True,
            "fields": {
                "D006.NOMER": {"meaning": "household join key", "unit": "identifier, not returned"},
                "D006.OB_PL": {"meaning": "total dwelling area", "unit": "square metres"},
                "D006.J_PL": {"meaning": "living area, used for sensitivity check", "unit": "square metres"},
                "D008.NOMP": {"meaning": "person key within household", "unit": "identifier, not returned"},
                "D008.KOL_CHL": {"meaning": "declared household size; checked against distinct persons", "unit": "persons"}},
            "supported_options": dict(OPTIONS), "ai_planner_connected": False,
            "model_provider": "selected_by_external_client", "limitations": list(LIMITATIONS)}

    def preview(self, year=2024, grouping=OPTIONS["grouping"], aggregation="median") -> dict[str, Any]:
        options = validate_options(year, grouping, aggregation)
        return {"status": "plan_only", "options": options, "reads_data": False,
            "steps": ["Validate D006 and D008 keys, positive areas and roster counts.",
                "Join housing to household sizes within 2024.",
                "Calculate OB_PL / household size for each household.",
                "Report unweighted household medians in groups 1–2, 3–4 and 5+ persons.",
                "Separately recompute after excluding J_PL > OB_PL; original files remain unchanged."],
            "outputs": ["coverage", "primary", "sensitivity", "method", "input_sha256", "limitations"],
            "ai_planner_connected": False, "limitations": list(LIMITATIONS)}

    def calculate(self, year=2024, grouping=OPTIONS["grouping"], aggregation="median") -> dict[str, Any]:
        validate_options(year, grouping, aggregation)
        try:
            paths = {form: (self.data_dir / relative).resolve() for form, relative in SOURCES.items()}
            if any(not path.is_relative_to(self.data_dir) for path in paths.values()):
                raise ValueError("Source outside configured directory")
            before = {form: hashlib.sha256(path.read_bytes()).hexdigest() for form, path in paths.items()}
            result = calculate(load_rows(paths["D006"], ["NOMER", "OB_PL", "J_PL"]),
                load_rows(paths["D008"], ["NOMER", "NOMP", "KOL_CHL"]))
            after = {form: hashlib.sha256(path.read_bytes()).hexdigest() for form, path in paths.items()}
            if before != after:
                raise ValueError("Source changed during calculation")
            script_sha256 = hashlib.sha256(Path(__file__).resolve().parents[1].joinpath(
                "examples/research_brief/reproduce.py").read_bytes()).hexdigest()
        except Exception:
            # Source validation exceptions may contain household identifiers or local paths.
            raise ValueError("Calculation unavailable: configured sources are missing, changed, or failed validation. Ask the local operator to check the source files.") from None
        result["inputs"] = {form: {"path": SOURCES[form], "sha256": before[form]} for form in SOURCES}
        result["reproduction"] = {"module": "examples.research_brief.reproduce", "function": "calculate",
            "script_sha256": script_sha256}
        result["transport"] = "stdio"
        result["model_provider"] = "selected_by_external_client"
        result["limitations"].extend(LIMITATIONS[1:])
        return result


def initialize_optional_sdk_environment():
    """Honor .pth files in a --target install, including pywin32 on Windows."""
    spec = importlib.util.find_spec("mcp")
    if spec and spec.origin:
        site.addsitedir(str(Path(spec.origin).resolve().parents[1]))


def create_server(data_dir: Path):
    # Optional dependency: the normal Residual web app does not import the SDK.
    initialize_optional_sdk_environment()
    from mcp.server.fastmcp import FastMCP
    from mcp.types import CallToolResult, TextContent, ToolAnnotations

    class RestrictedMCP(FastMCP):
        async def call_tool(self, name, arguments):
            allowed = set() if name == "describe_housing_dataset" else set(OPTIONS)
            if name not in TOOL_NAMES or not isinstance(arguments or {}, dict) or set(arguments or {}) - allowed:
                return CallToolResult(isError=True, content=[TextContent(type="text",
                    text="Unsupported tool or arguments. File paths, raw records, code and arbitrary queries are not accepted.")])
            if name != "describe_housing_dataset":
                try:
                    validate_options(**(arguments or {}))
                except (TypeError, ValueError):
                    return CallToolResult(isError=True, content=[TextContent(type="text",
                        text="Unsupported options: only year 2024, fixed household-size groups and median are available.")])
            return await super().call_tool(name, arguments)

    server = RestrictedMCP("Residual housing brief", log_level="WARNING", instructions=
        "Read-only tools for one synthetic housing brief. First describe, then preview the fixed plan. "
        "Use calculate only for supported options. Results are unweighted synthetic examples, not official estimates. "
        "There is no model inside this server. Do not invent unsupported analyses or infer causality.")
    bridge = HousingBridge(data_dir)
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=annotations)
    def describe_housing_dataset() -> dict[str, Any]:
        """Describe the allowlisted synthetic 2024 forms, fields, units and scope; returns no records."""
        return bridge.describe()

    @server.tool(annotations=annotations)
    def preview_housing_brief(year: Literal[2024] = 2024,
            grouping: Literal["household_size_1_2_3_4_5_plus"] = OPTIONS["grouping"],
            aggregation: Literal["median"] = "median") -> dict[str, Any]:
        """Validate fixed options and show the exact housing comparison plan without reading data."""
        return bridge.preview(year, grouping, aggregation)

    @server.tool(annotations=annotations)
    def calculate_housing_brief(year: Literal[2024] = 2024,
            grouping: Literal["household_size_1_2_3_4_5_plus"] = OPTIONS["grouping"],
            aggregation: Literal["median"] = "median") -> dict[str, Any]:
        """Run the declared local calculation; return aggregates, source hashes and limitations, never household rows."""
        return bridge.calculate(year, grouping, aggregation)

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    create_server(args.data_dir).run(transport="stdio")


if __name__ == "__main__":
    main()
