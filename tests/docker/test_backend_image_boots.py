"""Smoke tests that build Dockerfile.backend and verify the resulting image can
actually start the app -- not just that `docker build` succeeds.

Regression coverage for a bug where Dockerfile.backend built fine (all wheels
installed) but the app itself failed at import time with
`ModuleNotFoundError: No module named 'config'`, because the COPY list never
included the `config/` package or `environments/*.yaml` that
`Storage/config.py` needs. A successful `docker build` alone doesn't catch
this -- the image has to actually be run.

Requires a local Docker daemon. Skipped automatically if one isn't reachable.
"""
import os
import shutil
import subprocess
import time
import uuid

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

IMAGE_TAG = "memes-backend-dockertest:latest"
DUMMY_ENV = [
    "-e", "DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/test",
    "-e", "APP_ENV=metal",
    "-e", "BASE_PATH=/tmp/memes-test-images",
]


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(
        ["docker", "info"], capture_output=True
    ).returncode == 0


def _run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, **kwargs)


@pytest.fixture(scope="module")
def built_image():
    if not _docker_available():
        pytest.skip("Docker daemon not available")

    result = _run(["docker", "build", "-f", "Dockerfile.backend", "-t", IMAGE_TAG, "."])
    if result.returncode != 0:
        pytest.fail(f"docker build failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    yield IMAGE_TAG

    _run(["docker", "rmi", "-f", IMAGE_TAG])


def test_image_imports_app_main(built_image):
    result = _run([
        "docker", "run", "--rm", *DUMMY_ENV, built_image,
        "python", "-c", "import Backend.app.main",
    ])
    assert result.returncode == 0, (
        f"Backend.app.main failed to import inside the built image "
        f"(missing COPY of a package it needs?):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_container_boots_and_stays_running(built_image):
    container_name = f"memes-backend-smoketest-{uuid.uuid4().hex[:8]}"
    run = _run(["docker", "run", "-d", "--name", container_name, *DUMMY_ENV, built_image])
    assert run.returncode == 0, f"docker run failed:\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"

    try:
        time.sleep(5)
        inspect = _run(["docker", "inspect", "-f", "{{.State.Running}}", container_name])
        logs = _run(["docker", "logs", container_name])
        assert inspect.stdout.strip() == "true", (
            f"container exited before staying up 5s. logs:\n{logs.stdout}\n{logs.stderr}"
        )
        assert "Traceback" not in logs.stderr, f"traceback in container logs:\n{logs.stderr}"
    finally:
        _run(["docker", "rm", "-f", container_name])
