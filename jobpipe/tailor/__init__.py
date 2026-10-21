"""Resume tailoring: schema, prompt, validation, and template context."""

from jobpipe.tailor.schema import Metric, NewProject, Keywords, TailorOutput
from jobpipe.tailor.layout import template_meta, skill_keys, project_count, project_bullet_count, projects_enabled, bullet_chars
from jobpipe.tailor.slots import offerable_projects, proposed_projects, reusable_pool_projects, unbuilt_cap_warning, apply_pool_reuse, project_header
from jobpipe.tailor.text import BOLD, NUM, plain, numbers, parse_bold
from jobpipe.tailor.prompt import style_prompt, lint_rules, experience_bullets, experience_text, build_prompt, retry_prompt
from jobpipe.tailor.validate import Validation, all_bullet_texts, built_bullets, fixed_texts, style_lint, STOPWORDS, repeated_terms, validate, drop_unlisted_skills, XYZ_CONNECTOR, xyz_problems
from jobpipe.tailor.context import skill_row_budget, fit_skill_rows, build_context, resume_text

__all__ = ["Metric", "NewProject", "Keywords", "TailorOutput", "template_meta", "skill_keys", "project_count", "project_bullet_count", "projects_enabled", "bullet_chars", "offerable_projects", "proposed_projects", "reusable_pool_projects", "unbuilt_cap_warning", "apply_pool_reuse", "project_header", "BOLD", "NUM", "plain", "numbers", "parse_bold", "style_prompt", "lint_rules", "experience_bullets", "experience_text", "build_prompt", "retry_prompt", "Validation", "all_bullet_texts", "built_bullets", "fixed_texts", "style_lint", "STOPWORDS", "repeated_terms", "validate", "drop_unlisted_skills", "XYZ_CONNECTOR", "xyz_problems", "skill_row_budget", "fit_skill_rows", "build_context", "resume_text"]
