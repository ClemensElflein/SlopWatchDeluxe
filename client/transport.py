import json
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(config, path, data=None, timeout=0.8, method=None):
    headers = {"Accept": "application/json", "User-Agent": "SlopWatchDeluxe/1.1.0"}
    if config.get("token"):
        headers["Authorization"] = "Bearer " + config["token"]
    body = None
    if data is not None:
        body = json.dumps(data, allow_nan=False).encode()
        if len(body) > 65536:
            raise ValueError("Normalized event exceeds 64 KiB")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(config["url"] + path, data=body, headers=headers, method=method)
    # Never forward tokens on redirects or send LAN events through inherited proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=timeout) as response:
        raw = response.read(131073)
        if len(raw) > 131072:
            raise ValueError("Server response exceeds 128 KiB")
        return json.loads(raw) if raw else None
