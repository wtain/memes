from pathlib import Path

from Backend.app.scheduler import _load_job_configs
from batch.registry import BatchRegistry
from config.settings import _build


def _names(environment):
    return [job["name"] for job in _load_job_configs(_build(environment))]


def test_general_schedules_the_gold_jobs_next_to_the_existing_ones():
    names = _names("general")
    assert "export_ratings" in names and "import_judgments" in names
    assert "trends_batch" in names and "build_statistics" in names


def test_other_environments_do_not_schedule_them():
    for environment in ("metal", "it"):
        assert "export_ratings" not in _names(environment)
        assert "import_judgments" not in _names(environment)


def test_both_jobs_are_registered_with_matching_kinds():
    registry = BatchRegistry(Path(__file__).resolve().parents[2] / "environments")
    for name in ("export_ratings", "import_judgments"):
        assert registry.get(name) == {"module": f"batch.{name}", "kind": name}


def test_intervals():
    jobs = {job["name"]: job for job in _load_job_configs(_build("general"))}
    assert jobs["export_ratings"]["interval_minutes"] == 1440
    assert jobs["import_judgments"]["interval_minutes"] == 10080
