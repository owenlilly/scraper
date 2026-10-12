import os
import subprocess
import sys


def run_python(code: str, cwd, **env) -> subprocess.CompletedProcess:
    """A fresh interpreter, so what the module does at import time is what we observe. It runs in `cwd`, a scratch directory,
    because configuration is also read from a `.env` in the working directory, and a test must never pick up a real key."""
    full_env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path), **env}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=full_env, timeout=60, cwd=cwd)


def test_importing_the_entry_point_switches_cloud_tracing_off_even_if_the_environment_turned_it_on(tmp_path):
    code = "import os, scraper_api.__main__; print(os.environ['LANGSMITH_TRACING'], os.environ['LANGCHAIN_TRACING_V2'])"
    result = run_python(code, tmp_path, LANGSMITH_TRACING="true", LANGCHAIN_TRACING_V2="true")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["false", "false"]


def test_it_refuses_to_start_outside_hosted_mode(tmp_path):
    result = run_python("from scraper_api.__main__ import main; main()", tmp_path, SCRAPER_HOSTED="false")
    assert result.returncode != 0 and "SCRAPER_HOSTED=true" in (result.stdout + result.stderr)


def test_it_refuses_to_start_hosted_without_its_secrets(tmp_path):
    env = {k: "" for k in os.environ if k.startswith(("SCRAPER_", "MISTRAL_"))}
    result = run_python("from scraper_api.__main__ import main; main()", tmp_path, SCRAPER_HOSTED="true", **env)
    output = result.stdout + result.stderr
    assert result.returncode != 0 and "SCRAPER_HOSTED needs" in output and "Traceback" not in output
