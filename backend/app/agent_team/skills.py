"""Skill Registry：发现并加载 Agent 可使用的 Markdown 调查技能。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class Skill:
    """磁盘上的一个技能文档。"""

    name: str
    title: str
    path: Path


class SkillRegistry:
    """管理 agent_team/skills 下的 Markdown 技能文件。"""

    def __init__(self, skill_dir: Path | None = None):
        self.skill_dir = skill_dir or Path(__file__).parent / "skills"
        self._skills = self._discover()

    def list(self) -> List[Dict[str, str]]:
        """返回技能名称和标题，供 LLM 工具调用展示。"""
        return [
            {"name": skill.name, "title": skill.title}
            for skill in sorted(self._skills.values(), key=lambda item: item.name)
        ]

    def load(self, name: str) -> str:
        """按名称读取技能正文；未知名称会抛出可解释错误。"""
        skill = self._skills.get(name)
        if not skill:
            known = ", ".join(sorted(self._skills))
            raise ValueError(f"Unknown skill '{name}'. Known skills: {known}")
        return skill.path.read_text(encoding="utf-8")

    def preload(self, names: List[str]) -> Dict[str, str]:
        """在系统提示中预加载指定技能，缺失技能会被跳过。"""
        loaded: Dict[str, str] = {}
        for name in names:
            try:
                loaded[name] = self.load(name)
            except ValueError:
                continue
        return loaded

    def _discover(self) -> Dict[str, Skill]:
        """扫描技能目录，把每个 .md 文件登记成一个 Skill。"""
        skills: Dict[str, Skill] = {}
        if not self.skill_dir.exists():
            return skills
        for path in self.skill_dir.glob("*.md"):
            name = path.stem
            first_line = path.read_text(encoding="utf-8").splitlines()[0:1]
            title = first_line[0].lstrip("# ").strip() if first_line else name
            skills[name] = Skill(name=name, title=title, path=path)
        return skills
