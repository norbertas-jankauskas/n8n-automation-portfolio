"""Trusted local Airtable read-back; importing this module makes no requests."""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


TARGET_HOST = "api.airtable.com"
BASE_ID = "appHNWJpExA6nWZks"
TABLE_ID = "tbly57I1Q5JJUyMlP"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Ignore ambient proxies and refuse redirects, which could forward the token.
urlopen = build_opener(ProxyHandler({}), NoRedirect()).open


def read_secret_file(path):
    """Read a regular, owned 0600 file without following a final symlink.

    Errors are deliberately constant and never include file contents or paths.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.getuid() or info.st_size > 65536):
                raise ValueError
            values = {}
            for line in source.read(65537).splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                name, separator, value = line.partition("=")
                if (not separator or name != "AIRTABLE_TOKEN" or name in values
                        or re.fullmatch(r"pat[A-Za-z0-9]{8,32}\.[A-Za-z0-9]{32,128}", value) is None):
                    raise ValueError
                values[name] = value
        key = values["AIRTABLE_TOKEN"]
        return key
    except (OSError, ValueError, KeyError, UnicodeError):
        raise ValueError("invalid secret file") from None


def readback(token):
    leads = []
    offset = None
    for _ in range(10):
        url = f"https://{TARGET_HOST}:443/v0/{BASE_ID}/{TABLE_ID}?pageSize=100"
        if offset is not None:
            url += "&offset=" + quote(offset, safe="")
        request = Request(url, headers={"Authorization": "Bearer " + token}, method="GET")
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise ValueError("invalid read-back response")
        for record in payload["records"]:
            if (not isinstance(record, dict) or not isinstance(record.get("fields"), dict)
                    or not isinstance(record["fields"].get("Email"), str)
                    or not record["fields"]["Email"]):
                raise ValueError("invalid read-back response")
            leads.append(dict(record["fields"]))
        if "offset" not in payload:
            return {"leads": sorted(leads, key=lambda row: row["Email"])}
        offset = payload["offset"]
        if not isinstance(offset, str) or not offset:
            raise ValueError("invalid read-back response")
    raise ValueError("invalid read-back response")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-file", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        key = read_secret_file(args.secret_file)
        result = readback(key)
        text = json.dumps(result, sort_keys=True, indent=2) + "\n"
        # A response must never turn the trusted read-back output into a key dump.
        from base64 import b64encode
        from urllib.parse import quote, quote_plus
        if any(value in text for value in (key, b64encode(key.encode()).decode(),
                                            quote(key, safe=""), quote_plus(key, safe=""))):
            raise ValueError("credential in response")
        Path(args.out).write_text(text, encoding="utf-8")
    except Exception:
        print("ERROR: read-back failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
