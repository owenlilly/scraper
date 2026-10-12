import socket
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_egress.py"


def run(*targets):
    return subprocess.run([sys.executable, str(SCRIPT), *targets], capture_output=True, text=True, timeout=30)


def test_a_reachable_target_fails_the_check():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = run(f"127.0.0.1:{listener.getsockname()[1]}")
    assert result.returncode == 1 and "OPEN" in result.stdout and "NOT blocked" in result.stdout


def test_an_unreachable_target_passes_the_check():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]  # nothing listens here once the socket closes
    result = run(f"127.0.0.1:{port}")
    assert result.returncode == 0 and "closed" in result.stdout


def test_one_open_target_among_closed_ones_fails():
    with socket.socket() as listener, socket.socket() as probe:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
        probe.close()
        result = run(f"127.0.0.1:{closed}", f"127.0.0.1:{listener.getsockname()[1]}")
    assert result.returncode == 1 and "1 of 2" in result.stdout


def test_a_malformed_target_is_a_clear_error():
    result = run("not-a-target")
    assert result.returncode != 0 and "host:port" in result.stderr
