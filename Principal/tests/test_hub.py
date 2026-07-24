import tempfile
import unittest
from pathlib import Path
import sys

PRINCIPAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRINCIPAL))

from catalog_data import get_catalog
from hub_db import HubDB
from hub_installer import AutoInstaller
from hub_paths import APP, BASE, github_repo_url, safe_tool_id, within


class PathTests(unittest.TestCase):
    def test_root_is_portable_and_absolute(self):
        self.assertTrue(BASE.is_absolute())
        self.assertEqual(BASE.name, "AICorte")

    def test_safe_tool_id(self):
        self.assertEqual(safe_tool_id("Meu App 2"), "meu-app-2")
        with self.assertRaises(ValueError):
            safe_tool_id("x")

    def test_github_url_is_canonical(self):
        self.assertEqual(
            github_repo_url("https://github.com/openai/whisper/tree/main?x=1"),
            "https://github.com/openai/whisper.git",
        )

    def test_path_escape_is_rejected(self):
        with self.assertRaises(ValueError):
            within(APP, BASE.parent / "outside")

    def test_archive_path_escape_is_rejected(self):
        with self.assertRaises(RuntimeError):
            AutoInstaller._safe_archive_members(["valid/file.exe", "../escape.exe"])


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=APP / "tmp")
        root = Path(self.temp.name)
        self.db = HubDB(root / "hub.sqlite3", root / "prompts.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_operation_lifecycle(self):
        operation_id = self.db.create_operation("demo-app", "install", {"trusted": True})
        operation = self.db.operation(operation_id)
        self.assertEqual(operation["status"], "queued")
        self.db.update_operation(operation_id, status="running", progress=50, phase="build")
        self.db.finish_operation(operation_id, "completed", "Pronto", {"ok": True})
        operation = self.db.operation(operation_id)
        self.assertEqual(operation["status"], "completed")
        self.assertTrue(operation["result"]["ok"])

    def test_favorites_recents_settings_and_prompts(self):
        self.db.set_favorite("demo-app", True)
        self.db.touch_recent("demo-app")
        self.db.set_setting("view", "list")
        self.db.save_prompt("demo", "field", "texto")
        self.assertEqual(self.db.favorites(), ["demo-app"])
        self.assertEqual(self.db.recents()[0]["tool_id"], "demo-app")
        self.assertEqual(self.db.get_settings()["view"], "list")
        self.assertEqual(self.db.prompt_values("demo")["field"], "texto")

    def test_live_backup(self):
        self.db.set_setting("density", "compact")
        created = self.db.backup()
        self.assertEqual(len(created), 2)
        self.assertTrue(all(Path(path).is_file() for path in created))


class CatalogAndFrontendTests(unittest.TestCase):
    def test_catalog_has_valid_docker_apps(self):
        catalog = get_catalog()
        self.assertEqual(catalog["max_running"], 0)
        managed = [tool for tool in catalog["tools"] if tool["install_kind"] == "docker"]
        self.assertEqual(
            {tool["id"] for tool in managed},
            {
                "ollama", "open-llm-vtuber", "n8n", "open-webui", "langflow", "memos", "ntfy",
                "qwenpaw", "open-notebook", "trek", "reclip", "whaticket-community",
            },
        )
        for tool in managed:
            self.assertTrue(tool["detached"])
            self.assertTrue(Path(tool["docker_compose"]).is_file())

    def test_every_supported_catalog_item_has_a_managed_recipe(self):
        tools = get_catalog()["tools"]
        blocked = {tool["id"] for tool in tools if tool["availability"] == "blocked"}
        self.assertEqual(blocked, {"dory", "mac-sai"})
        for tool in tools:
            if tool["id"] in blocked:
                self.assertFalse(tool["install_managed"])
                self.assertEqual(tool["install_kind"], "unsupported")
                continue
            self.assertTrue(tool["install_managed"])
            self.assertIn(tool["install_kind"], {"docker", "source", "release"})
            self.assertTrue(tool["repo"])

    def test_requested_explore_catalog_is_present(self):
        tools = get_catalog()["tools"]
        requested = {
            "OpenCut", "OpenMontage", "HyperFrames", "ReClip", "SnapOtter", "CompressO",
            "Modly", "LibreChat", "Open WebUI", "Open-LLM-VTuber", "Open Higgsfield AI",
            "PenEcho", "QwenPaw", "Observer AI", "DeerFlow", "Raven", "Agentic Inbox",
            "Open Notebook", "Yuvomi", "Trek", "Memos", "Seafile", "Instatic",
            "SimpleX Chat", "ntfy", "AdGuard Home", "Logto", "Floci", "Databasement",
            "Duplicati", "Velero", "Dory", "docker-android", "Mac Sai", "Mouzi",
            "FileExplorer", "Superfile", "VeloxDB", "Bruno", "VoidAccess", "Maigret",
            "Scout", "Unblink", "OpenScholarXIV", "PaperBanana", "olmOCR 2", "TextSnap",
            "PixelRAG", "HyperExtract", "Graphify", "HelixDB", "OpenClaw", "AutoGPT",
            "ComfyUI", "Ollama", "Kilo Code", "Peacock", "WhaTicket Community",
        }
        self.assertEqual(len(tools), 60)
        self.assertEqual(len({tool["id"] for tool in tools}), len(tools))
        self.assertTrue(requested.issubset({tool["name"] for tool in tools}))
        self.assertTrue(all(tool["availability"] == "blocked" for tool in tools if not tool["install_managed"]))

    def test_catalog_paths_stay_in_selected_root(self):
        for tool in get_catalog()["tools"]:
            if tool.get("path"):
                Path(tool["path"]).resolve().relative_to(BASE)

    def test_frontend_exposes_download_and_remove(self):
        principal = Path(__file__).resolve().parents[1]
        html = (principal / "index.html").read_text(encoding="utf-8")
        script = (principal / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('data-view="install"', html)
        self.assertIn('id="view-maintenance"', html)
        self.assertIn('tool.install_label || "Download"', script)
        self.assertIn(">Remover</button>", script)
        self.assertIn("window.open(tool.url", script)
        self.assertNotIn("Receita pendente", script)

    def test_compose_storage_uses_selected_root(self):
        principal = Path(__file__).resolve().parents[1]
        for compose in (principal / "docker").glob("*/compose.yaml"):
            content = compose.read_text(encoding="utf-8")
            if "volumes:" in content:
                self.assertIn("${AICORTE_ROOT_WSL}", content)

    def test_native_shell_and_bootstrap_exist(self):
        principal = Path(__file__).resolve().parents[1]
        self.assertTrue((BASE / "AICorte.exe").is_file())
        self.assertTrue((principal / "native" / "AICorteShell.cs").is_file())
        self.assertTrue((principal / "scripts" / "bootstrap-environment.ps1").is_file())

    def test_native_shell_owns_local_dashboard_and_webview(self):
        principal = Path(__file__).resolve().parents[1]
        shell = (principal / "native" / "AICorteShell.cs").read_text(encoding="utf-8")
        self.assertIn('DashboardUrl = "http://127.0.0.1:8787"', shell)
        self.assertIn("new WebView2()", shell)
        self.assertIn("OpenDashboardAsync", shell)

if __name__ == "__main__":
    unittest.main()
