"""Run this on the machine that hosts the API to prove it cannot reach private addresses.

    python scripts/check_egress.py 10.0.0.5:5432 169.254.169.254:80

Each argument is host:port of something that WOULD answer if the network let the packet through (your database's
private address, the cloud metadata address). The check passes only when every connection fails. A connection that
succeeds means egress is open and the application-level address check is the only thing standing in the way."""

import socket
import sys

TIMEOUT_SECONDS = 3.0
DEFAULT_PROBES = ["169.254.169.254:80"]


def parse(target: str) -> tuple[str, int]:
    host, _, port = target.rpartition(":")
    if not host or not port.isdigit():
        raise SystemExit(f"Expected host:port, got {target!r}")
    return host.strip("[]"), int(port)


def is_open(host: str, port: int, timeout: float = TIMEOUT_SECONDS) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main(argv: list[str]) -> int:
    targets = argv or DEFAULT_PROBES
    leaks = 0
    for target in targets:
        host, port = parse(target)
        if is_open(host, port):
            print(f"OPEN    {target}: the connection succeeded, so egress to it is NOT blocked")
            leaks += 1
        else:
            print(f"closed  {target}")
    if leaks:
        print(f"\nFAIL: {leaks} of {len(targets)} private target(s) are reachable from this host.")
        return 1
    print(f"\nOK: none of {len(targets)} target(s) could be reached. Make sure each one would answer from somewhere else.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
