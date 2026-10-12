"""Core checks run without MCP; optional integration uses the official SDK."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from src.mcp_server import HousingBridge, initialize_optional_sdk_environment


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.housing = self.root / "d006/2024/rio_2024.csv"
        self.roster = self.root / "d008/2024/kontr_k.csv"
        self.housing.parent.mkdir(parents=True)
        self.roster.parent.mkdir(parents=True)
        self.housing.write_text("NOMER,OB_PL,J_PL\nPRIVATE_HOUSEHOLD_A,10,6\nPRIVATE_HOUSEHOLD_B,100,110\n", encoding="utf-8")
        self.roster.write_text("NOMER,NOMP,KOL_CHL\nPRIVATE_HOUSEHOLD_A,01,1\nPRIVATE_HOUSEHOLD_B,01,2\nPRIVATE_HOUSEHOLD_B,02,2\n", encoding="utf-8")
        self.bridge = HousingBridge(self.root)

    def test_exact_calculation_aggregates_and_no_source_mutation(self):
        original = self.housing.read_bytes(), self.roster.read_bytes()
        result = self.bridge.calculate()
        self.assertEqual(result["primary"][0]["median_area_per_person_m2"], 30)
        self.assertEqual(result["sensitivity"][0]["median_area_per_person_m2"], 10)
        self.assertFalse(result["ai_planner_connected"])
        self.assertEqual(result["coverage"]["area_order_flagged_households"], 1)
        self.assertEqual(original, (self.housing.read_bytes(), self.roster.read_bytes()))
        wire = json.dumps(result)
        self.assertNotIn("PRIVATE_HOUSEHOLD", wire)
        self.assertNotIn(str(self.root), wire)
        self.assertEqual(len(result["inputs"]["D006"]["sha256"]), 64)

    def test_reject_unsupported_methods_year_and_file_arguments(self):
        for options in ({"year": 2023}, {"grouping": "region"}, {"aggregation": "mean"}):
            with self.assertRaises(ValueError):
                self.bridge.preview(**options)
            with self.assertRaises(ValueError):
                self.bridge.calculate(**options)
        with self.assertRaises(TypeError):
            self.bridge.calculate(file_path="C:/secret.csv")

    def test_source_error_does_not_reveal_identifier_or_local_path(self):
        self.roster.write_text(self.roster.read_text() + "PRIVATE_HOUSEHOLD_A,01,1\n", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            self.bridge.calculate()
        self.assertNotIn("PRIVATE_HOUSEHOLD", str(caught.exception))
        self.assertNotIn(str(self.root), str(caught.exception))

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "optional MCP SDK not installed")
    def test_official_stdio_client_round_trip_and_restrictions(self):
        initialize_optional_sdk_environment()
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def exercise():
            repo = Path(__file__).resolve().parents[1]
            parameters = StdioServerParameters(command=sys.executable,
                args=["-X", "utf8", "-m", "src.mcp_server", "--data-dir", str(self.root)],
                cwd=str(repo), env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    self.assertEqual({tool.name for tool in listing.tools},
                        {"describe_housing_dataset", "preview_housing_brief", "calculate_housing_brief"})
                    for tool in listing.tools:
                        self.assertTrue(tool.annotations.readOnlyHint)
                        self.assertTrue(tool.annotations.idempotentHint)
                        self.assertEqual(tool.outputSchema["type"], "object")
                    description = await session.call_tool("describe_housing_dataset", {})
                    self.assertFalse(description.structuredContent["ai_planner_connected"])
                    preview = await session.call_tool("preview_housing_brief", {})
                    self.assertFalse(preview.isError)
                    result = await session.call_tool("calculate_housing_brief", {})
                    self.assertFalse(result.isError)
                    self.assertEqual(result.structuredContent["primary"][0]["median_area_per_person_m2"], 30)
                    wire = result.model_dump_json()
                    self.assertNotIn("PRIVATE_HOUSEHOLD", wire)
                    self.assertNotIn(str(self.root), wire)
                    for name, args in [("calculate_housing_brief", {"year": 2023}),
                            ("calculate_housing_brief", {"year": "C:/secret.csv"}),
                            ("calculate_housing_brief", {"file_path": "C:/secret.csv"}),
                            ("describe_housing_dataset", {"raw_rows": True})]:
                        denied = await session.call_tool(name, args)
                        self.assertTrue(denied.isError)
                        self.assertNotIn("secret.csv", denied.model_dump_json())
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
