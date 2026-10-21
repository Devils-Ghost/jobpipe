"""Output schema of the tailoring step."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Metric(BaseModel):
    claim: str
    how_to_measure: str = ""


class NewProject(BaseModel):
    slug: str
    name: str = ""
    why: str = ""
    stack: list[str] = Field(default_factory=list)
    resume_bullets: list[str] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    milestones: list[str] = Field(default_factory=list)
    repo: str = ""   # set from the pool entry once the project has its own GitHub URL


class Keywords(BaseModel):
    must_have_matched: list[str] = Field(default_factory=list)
    must_have_missing: list[str] = Field(default_factory=list)
    nice_matched: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)


class TailorOutput(BaseModel):
    skills: dict[str, list[str]]   # one list per skills row key (template.json skill_keys)
    projects_used: list[str]       # built project ids and/or new project slugs, best fit first
    new_projects: list[NewProject] = Field(default_factory=list)
    keywords: Keywords = Field(default_factory=Keywords)
