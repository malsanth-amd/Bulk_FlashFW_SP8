#!/usr/bin/env python3
"""
AC-cycle a system via the FVDDR System Tracker API (http://10.148.170.16:3000).

Usage:
    python ac_cycle.py <system-name>
    python ac_cycle.py <system-name> --delay 30     # off, wait 30s, on (like "AC M-Delay")
    python ac_cycle.py <system-name> --token <admin-token>

With no --delay, performs a single "ac_cycle" action (same as the UI's "AC" button) -
the server handles the off/on sequencing itself.
"""
import argparse
import sys
import time

import requests

BASE_URL = "http://10.148.170.16:3000"


def get_systems(base_url):
    resp = requests.get(f"{base_url}/api/systems", timeout=15)
    resp.raise_for_status()
    return resp.json()


def find_system(systems, name):
    name_lc = name.strip().lower()
    matches = [s for s in systems if s.get("name", "").lower() == name_lc]
    if not matches:
        matches = [s for s in systems if name_lc in s.get("name", "").lower()]
    return matches


def power_action(base_url, sid, action, token=None, timeout=180):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Admin-Token"] = token
    resp = requests.post(f"{base_url}/api/systems/{sid}/power",
                          json={"action": action}, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("authFailed"):
        raise RuntimeError("Auth failed - PDU credentials required (pass --token, or set them up in the UI)")
    if not data.get("success"):
        raise RuntimeError(data.get("error") or "power action failed")
    return data


def main():
    parser = argparse.ArgumentParser(description="AC-cycle a system by name via FVDDR System Tracker")
    parser.add_argument("system", help="System name (e.g. Eagle-0291), partial match allowed")
    parser.add_argument("--delay", type=int, default=None,
                         help="If set, do off -> wait N seconds -> on, instead of single ac_cycle action")
    parser.add_argument("--token", default=None, help="X-Admin-Token header value, if required")
    parser.add_argument("--base-url", default=BASE_URL, help=f"API base URL (default: {BASE_URL})")
    parser.add_argument("--timeout", type=int, default=180,
                         help="HTTP request timeout in seconds per power call (default: 180)")
    parser.add_argument("--sut-wait", type=int, default=180,
                         help="Seconds to wait after the cycle for the SUT to come back online (default: 180, 0 to skip)")
    args = parser.parse_args()

    print(f"Looking up '{args.system}' at {args.base_url} ...")
    systems = get_systems(args.base_url)
    matches = find_system(systems, args.system)

    if not matches:
        print(f"No system found matching '{args.system}'", file=sys.stderr)
        sys.exit(1)
    if len(matches) > 1:
        print(f"Multiple systems match '{args.system}':", file=sys.stderr)
        for s in matches:
            print(f"  - {s['name']} (id={s['id']})", file=sys.stderr)
        sys.exit(1)

    system = matches[0]
    sid, name = system["id"], system["name"]
    print(f"Found: {name} (id={sid})")

    try:
        if args.delay is None:
            print("Triggering AC cycle...")
            power_action(args.base_url, sid, "ac_cycle", token=args.token, timeout=args.timeout)
            print(f"AC cycle complete for {name}")
        else:
            print(f"Turning {name} off...")
            power_action(args.base_url, sid, "off", token=args.token, timeout=args.timeout)
            print(f"Waiting {args.delay}s...")
            time.sleep(args.delay)
            print(f"Turning {name} on...")
            power_action(args.base_url, sid, "on", token=args.token, timeout=args.timeout)
            print(f"AC cycle complete for {name}")

        if args.sut_wait > 0:
            print(f"Wait for {args.sut_wait}sec for SUT back online")
            time.sleep(args.sut_wait)
    except requests.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
