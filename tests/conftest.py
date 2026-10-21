"""Tests run against a temp copy of the fictional sample profile (examples/sample_profile), never
the real profile/. The environment variable is set at import time, before any jobpipe module
reads settings, and the sample resumes' templates are built once per session."""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "examples" / "sample_profile"

_TMP = Path(tempfile.mkdtemp(prefix="jobpipe-test-profile-"))
PROFILE = _TMP / "profile"
shutil.copytree(SAMPLE, PROFILE)
os.environ["JOBPIPE_PROFILE"] = str(PROFILE)


@pytest.fixture(scope="session", autouse=True)
def sample_templates():
    from jobpipe import config, resume
    from jobpipe.render.template import build_template

    config.reload()
    for r in resume.all_resumes():
        build_template(r.master, r.template)
    yield
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture
def ds():
    """Run the test with the `ds` resume (has projects) active."""
    from jobpipe import resume

    with resume.use(resume.get("ds")) as r:
        yield r


@pytest.fixture
def analytics():
    """Run the test with the `analytics` resume (no projects section) active."""
    from jobpipe import resume

    with resume.use(resume.get("analytics")) as r:
        yield r
