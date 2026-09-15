"""Check discoverable metadata and navigable resources, not instruction wording."""

import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".agents/skills"
DAILY_LOG = SKILLS / "practicum-daily-log"


class AgentSkillTests(unittest.TestCase):
    def test_skill_metadata_loads(self):
        entries = list(SKILLS.glob("*/SKILL.md"))
        self.assertTrue(entries, "No discoverable skills")
        for path in entries:
            with self.subTest(skill=path.parent.name):
                content = path.read_text(encoding="utf-8")
                self.assertTrue(content.startswith("---\n"))
                frontmatter, separator, _ = content[4:].partition("\n---\n")
                self.assertTrue(separator, "Unclosed frontmatter")
                metadata = yaml.safe_load(frontmatter)
                self.assertIsInstance(metadata, dict)
                self.assertEqual(metadata["name"], path.parent.name)
                self.assertIsInstance(metadata["description"], str)
                self.assertTrue(metadata["description"].strip())
                config = path.parent / "agents/openai.yaml"
                if config.exists():
                    config_data = yaml.safe_load(config.read_text(encoding="utf-8"))
                    self.assertIsInstance(config_data, dict)
                    policy = config_data.get("policy", {})
                    if "allow_implicit_invocation" in policy:
                        self.assertIsInstance(policy["allow_implicit_invocation"], bool)

    def test_instruction_links_and_standalone_log_resources(self):
        paths = [ROOT / "AGENTS.md", *SKILLS.rglob("*.md"), *(ROOT / "docs/agents").glob("*.md")]
        for path in paths:
            content = path.read_text(encoding="utf-8")
            content = re.sub(r"(?ms)^```[^\n]*\n.*?^```[^\n]*$", "", content)
            # ponytail: inline Markdown links only; add a parser if reference-style routing is introduced.
            for link in re.findall(r"\[[^\]\n]*\]\(([^\s)]+)\)", content):
                url = urlsplit(link)
                if url.scheme or url.netloc or not url.path:
                    continue
                with self.subTest(file=str(path.relative_to(ROOT)), link=link):
                    target = (path.parent / unquote(url.path)).resolve()
                    self.assertTrue(target.exists(), f"Missing resource: {target}")
                    if path.is_relative_to(DAILY_LOG):
                        self.assertTrue(target.is_relative_to(DAILY_LOG), "Log skill depends on repository files")


if __name__ == "__main__":
    unittest.main()
