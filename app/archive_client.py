"""Strict Archive access. Credentials never follow redirects or supplied URLs."""
import copy
import json
import re
import urllib.error
import urllib.parse
import urllib.request

PRODUCTION = "https://archive.materialscloud.org"
IDENTIFIER = r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"
MAX_RESPONSE = 8 * 1024 * 1024
MAX_PAGE_SIZE = 100
MAX_COMMENT_BYTES = 50 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Archive:
    def __init__(self, base=PRODUCTION, token=""):
        base = base.rstrip("/")
        parsed = urllib.parse.urlsplit(base)
        if base != PRODUCTION and not (
            parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")
            and not parsed.username and not parsed.password and not parsed.path
            and not parsed.query and not parsed.fragment
        ):
            raise ValueError("Archive must be Materials Cloud or an explicit localhost mock.")
        self.base = base
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, path):
        if not self._safe_get_path(path):
            raise ValueError("This Archive endpoint is not allowed.")
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(self.base + path, headers=headers, method="GET")
        try:
            with self.opener.open(request, timeout=25) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as error:
            error.close()
            raise
        if len(raw) > MAX_RESPONSE:
            raise ValueError("Archive response is too large to review here.")
        return json.loads(raw)

    @staticmethod
    def _safe_get_path(path):
        """Allow only the small read-only API surface used by the workspace."""
        if not isinstance(path, str) or not path.startswith("/api/"):
            return False
        parsed = urllib.parse.urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            return False
        route = parsed.path
        if route == "/api/me":
            return not parsed.query
        if re.fullmatch(rf"/api/requests/{IDENTIFIER}", route):
            return not parsed.query
        if re.fullmatch(rf"/api/requests/{IDENTIFIER}/timeline", route):
            return Archive._safe_search_query(parsed.query, {"q", "sort", "size", "page"})
        if route in ("/api/requests", "/api/requests/"):
            return Archive._safe_search_query(parsed.query, {"q", "sort", "size", "page"})
        if re.fullmatch(rf"/api/records/{IDENTIFIER}(?:/draft)?(?:/files)?", route):
            return not parsed.query
        if route == "/api/records":
            return Archive._safe_search_query(parsed.query, {"q", "sort", "size", "page"})
        return False

    @staticmethod
    def _safe_search_query(query, allowed):
        try:
            values = urllib.parse.parse_qs(query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            return False
        if not values or not set(values).issubset(allowed) or any(len(value) != 1 for value in values.values()):
            return False
        for key in ("size", "page"):
            if key in values:
                raw = values[key][0]
                if not raw.isdigit():
                    return False
                number = int(raw)
                if number < 1 or (key == "size" and number > MAX_PAGE_SIZE):
                    return False
        if "sort" in values and values["sort"][0] not in ("newest", "oldest", "bestmatch"):
            return False
        return all(value[0] and not any(char in value[0] for char in "\r\n#")
                   for value in values.values())

    def me(self):
        return self.get("/api/me")

    def requests(self, query, sort="newest", size=100, page=1):
        params = urllib.parse.urlencode({"q": query, "sort": sort, "size": size, "page": page})
        return self.get("/api/requests/?" + params)["hits"]

    def request(self, request_id):
        if not re.fullmatch(IDENTIFIER, str(request_id)):
            raise ValueError("Invalid request identifier.")
        return self.get("/api/requests/" + str(request_id))

    def timeline(self, request_id, size=100, page=1):
        if not re.fullmatch(IDENTIFIER, str(request_id)):
            raise ValueError("Invalid request identifier.")
        params = urllib.parse.urlencode({"sort": "oldest", "size": size, "page": page})
        return self.get(f"/api/requests/{request_id}/timeline?{params}")["hits"]

    def record(self, record_id, draft=True):
        if not re.fullmatch(IDENTIFIER, str(record_id)):
            raise ValueError("Invalid record identifier.")
        suffix = "/draft" if draft else ""
        record = copy.deepcopy(self.get(f"/api/records/{record_id}{suffix}"))
        if record.get("id") != record_id:
            raise ValueError("The Archive returned a different record. Check the link again.")
        if record.get("files", {}).get("enabled", True):
            files = self.get(f"/api/records/{record_id}{suffix}/files")
            record.setdefault("files", {})["entries"] = files["entries"]
        else:
            record.setdefault("files", {})["entries"] = {}
        return validate_record(record)
    def resolve(self, text):
        text = text.strip()
        if text.startswith("{"):
            return validate_record(json.loads(text)), {
                "kind": "json", "label": "Pasted JSON · not verified against the Archive",
                "url": None, "request_id": None,
            }
        kind = "record"
        identifier = text
        if "://" in text:
            parsed = urllib.parse.urlsplit(text)
            if f"{parsed.scheme}://{parsed.netloc}" not in (PRODUCTION, self.base):
                raise ValueError("Use a link from archive.materialscloud.org.")
            request = re.fullmatch(rf"/communities/mcarchive/requests/({IDENTIFIER})/?", parsed.path)
            record = re.fullmatch(rf"/(?:api/)?records?/({IDENTIFIER}|\d{{4}}\.\d+)(?:/draft)?/?", parsed.path)
            if request:
                kind, identifier = "request", request.group(1)
            elif record:
                identifier = record.group(1)
            else:
                raise ValueError("Paste a community request link or a record link.")
        if re.fullmatch(r"\d{4}\.\d+", identifier):
            query = urllib.parse.quote(f'pids.mcid.identifier:"{identifier}"', safe="")
            hits = self.get("/api/records?q=" + query)["hits"]["hits"]
            if len(hits) != 1:
                raise ValueError("The Materials Cloud ID did not resolve to exactly one record.")
            identifier = str(hits[0]["id"])
        if not re.fullmatch(IDENTIFIER, identifier):
            raise ValueError("Enter a valid Archive request URL, record URL, record ID, or JSON.")
        context = {"kind": kind, "request_id": None}
        rid = identifier
        if kind == "request":
            request = self.request(identifier)
            if request.get("id") != identifier:
                raise ValueError("The Archive returned a different request. Check the link again.")
            rid = request.get("topic", {}).get("record")
            if not isinstance(rid, str) or not re.fullmatch(IDENTIFIER, rid):
                raise ValueError("This request has no supported record topic.")
            context.update(request_id=identifier, request_status=request.get("status", "unknown"),
                           is_open=request.get("is_open") is True,
                           request_type=request.get("type"), request_updated=request.get("updated"),
                           url=self.base + "/communities/mcarchive/requests/" + identifier)
        path = "/api/records/" + rid + "/draft"
        try:
            record = self.get(path)
        except urllib.error.HTTPError as error:
            # Don't let a denied draft silently fall back to the published record.
            if kind == "request" or error.code != 404:
                raise
            path = "/api/records/" + rid
            record = self.get(path)
        record = copy.deepcopy(record)
        if record.get("id") != rid:
            raise ValueError("The Archive returned a different record. Check the link again.")
        if record.get("files", {}).get("enabled", True):
            files = self.get(path + "/files")
            record.setdefault("files", {})["entries"] = files["entries"]
        else:
            record.setdefault("files", {})["entries"] = {}
        record = validate_record(record)
        context.update(record_id=rid, label=identifier,
                       draft=path.endswith("/draft"))
        context.setdefault("url", self.base + "/records/" + rid)
        return record, context


class CommentWriter:
    """The only production mutation supported by the dashboard: create one comment."""

    def __init__(self, archive):
        if not isinstance(archive, Archive):
            raise TypeError("CommentWriter requires a validated Archive client.")
        self.archive = archive

    def post(self, request_id, content_html):
        if not re.fullmatch(IDENTIFIER, str(request_id)):
            raise ValueError("Invalid request identifier.")
        if not self.archive.token:
            raise ValueError("A moderator token is required to send a reply.")
        if not isinstance(content_html, str) or not content_html.strip():
            raise ValueError("The reply is empty.")
        body = json.dumps({
            "payload": {"content": content_html, "format": "html"}
        }, ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_COMMENT_BYTES:
            raise ValueError("The reply is too long to send safely.")
        request = urllib.request.Request(
            self.archive.base + f"/api/requests/{request_id}/comments",
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + self.archive.token,
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with self.archive.opener.open(request, timeout=25) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as error:
            error.close()
            raise
        if len(raw) > MAX_RESPONSE:
            raise ValueError("Archive response is too large to validate.")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("type") != "C":
            raise ValueError("The Archive did not confirm creation of a comment.")
        return result


def validate_record(record):
    if not isinstance(record, dict) or not isinstance(record.get("metadata"), dict):
        raise ValueError("Expected a record JSON object with metadata.")
    files = record.get("files")
    if not isinstance(files, dict) or "entries" not in files:
        raise ValueError("File metadata is missing. Include files.entries so size and file checks are reliable.")
    entries = files["entries"]
    if isinstance(entries, dict):
        entries = [dict(value, key=key) for key, value in entries.items() if isinstance(value, dict)]
        if len(entries) != len(files["entries"]):
            raise ValueError("Invalid file metadata.")
    if not isinstance(entries, list):
        raise ValueError("files.entries must be a list or object.")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
            raise ValueError("Every file must have a name.")
        size = entry.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("Every file must have a non-negative integer size; incomplete uploads cannot be reviewed.")
        if entry.get("status") not in (None, "completed"):
            raise ValueError("Files are still uploading. Wait for completion, then check again.")
    if files.get("count") is not None and files["count"] != len(entries):
        raise ValueError("File listing is incomplete. Reload the record before reviewing.")
    return record
