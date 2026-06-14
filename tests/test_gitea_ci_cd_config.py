from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW_FILE = ROOT / ".gitea" / "workflows" / "ci.yml"
DEPLOY_WORKFLOW_FILE = ROOT / ".gitea" / "workflows" / "deploy.yml"
DOC_FILE = ROOT / "docs" / "ci-cd-gitea.md"


def _workflow(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _step_values(job: dict, key: str) -> list[str]:
    return [str(step.get(key, "")) for step in job["steps"] if key in step]


def test_gitea_workflow_runs_tests_and_builds_images():
    workflow = _workflow(CI_WORKFLOW_FILE)

    triggers = workflow["on"]
    assert "push" not in triggers
    assert triggers["pull_request"]["branches"] == ["master"]
    assert "workflow_dispatch" in triggers

    test_job = workflow["jobs"]["test"]
    assert test_job["runs-on"] == "ubuntu-latest"

    uses_values = _step_values(test_job, "uses")
    assert "actions/checkout@v4" in uses_values
    assert "astral-sh/setup-uv@v5" in uses_values
    assert "actions/setup-python@v5" in uses_values

    run_script = "\n".join(_step_values(test_job, "run"))
    assert "uv sync --frozen" in run_script
    assert "PYTHONPATH=. uv run pytest -q" in run_script
    assert "docker build -t python-strm:ci ." in run_script
    assert "docker build -t pansou-src:ci ./pansou_src" in run_script


def test_gitea_workflow_deploys_master_by_ssh_after_tests():
    workflow = _workflow(DEPLOY_WORKFLOW_FILE)
    deploy_job = workflow["jobs"]["deploy"]

    assert deploy_job["needs"] == ["test"]
    assert workflow["on"]["push"]["branches"] == ["master"]

    env = deploy_job["env"]
    assert env["DEPLOY_HOST"] == "${{ secrets.DEPLOY_HOST }}"
    assert env["DEPLOY_PORT"] == "${{ secrets.DEPLOY_PORT }}"
    assert env["DEPLOY_USER"] == "${{ secrets.DEPLOY_USER }}"
    assert env["DEPLOY_SSH_KEY"] == "${{ secrets.DEPLOY_SSH_KEY }}"
    assert env["DEPLOY_PATH"] == "${{ secrets.DEPLOY_PATH }}"

    test_job = workflow["jobs"]["test"]
    test_script = "\n".join(_step_values(test_job, "run"))
    assert "PYTHONPATH=. uv run pytest -q" in test_script
    assert "docker build -t python-strm:ci ." in test_script
    assert "docker build -t pansou-src:ci ./pansou_src" in test_script

    deploy_script = "\n".join(_step_values(deploy_job, "run"))
    assert "test -n \"$DEPLOY_HOST\"" in deploy_script
    assert "test -n \"$DEPLOY_USER\"" in deploy_script
    assert "test -n \"$DEPLOY_SSH_KEY\"" in deploy_script
    assert "test -n \"$DEPLOY_PATH\"" in deploy_script
    assert "ssh-keyscan" in deploy_script
    assert "git pull --ff-only origin master" in deploy_script
    assert "docker compose build" in deploy_script
    assert "docker compose up -d --remove-orphans" in deploy_script


def test_gitea_ci_cd_documentation_lists_private_repo_setup():
    doc = DOC_FILE.read_text(encoding="utf-8")

    for text in [
        "Gitea Actions",
        "ubuntu-latest",
        "DEPLOY_HOST",
        "DEPLOY_USER",
        "DEPLOY_SSH_KEY",
        "DEPLOY_PATH",
        "私有仓库",
        "docker compose up -d --remove-orphans",
    ]:
        assert text in doc


def test_dockerignore_keeps_ci_and_runtime_state_out_of_images():
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    for pattern in [".gitea/", ".pytest_cache/", ".codex/", "strm/"]:
        assert pattern in dockerignore
