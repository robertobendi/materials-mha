"""Dashboard projections, plus replies built from the historical templates."""
import datetime
import hashlib
import html
import json
import os
import re
import urllib.error


COMMUNITY_ID = "30074283-2e48-4526-b2df-c3c2191db3cb"
COMMUNITY_SLUG = "mcarchive"
HISTORICAL_MODERATOR_ID = "1965"

SECTION_BY_KEY = {
    "t": "Title",
    "a": "Authors",
    "af": "Affiliations",
    "d": "Description",
    "f": "Files",
    "ref": "References",
    "k": "Keywords",
    "rt": "Resource type",
    "l": "Licenses",
    "mc": "Materials Cloud sections",
    "SIZE": "Files",
}


def load_playbook(root):
    with open(os.path.join(root, "rules", "precedents.json"), encoding="utf-8") as source:
        return json.load(source)


def plain(value):
    text = re.sub(r"</(?:p|div|li)>|<br\s*/?>", "\n", str(value or ""), flags=re.I)
    text = re.sub(r"<[^>]*>", "", text)
    text = html.unescape(text).replace("\r", "")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def reply_html(value):
    """Convert an explicitly confirmed plain-text draft to small, safe HTML."""
    if not isinstance(value, str):
        raise ValueError("The reply must be plain text.")
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        raise ValueError("The reply is empty.")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError("The reply contains unsupported control characters.")
    paragraphs = []
    for paragraph in re.split(r"\n{2,}", value):
        escaped = html.escape(paragraph.strip(), quote=True).replace("\n", "<br>")
        if escaped:
            paragraphs.append("<p>" + escaped + "</p>")
    return "".join(paragraphs)


def timeline_fingerprint(timeline):
    """Fingerprint the complete raw timeline so a new reply invalidates a send."""
    return hashlib.sha256(json.dumps(
        timeline, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _identity(me):
    email = str(me.get("email") or "")
    username = str(me.get("username") or (email.split("@", 1)[0] if email else "moderator"))
    profile = me.get("profile") if isinstance(me.get("profile"), dict) else {}
    display = str(profile.get("full_name") or "").strip()
    if not display:
        display = " ".join(part.capitalize() for part in re.split(r"[._-]+", username) if part)
    return {"id": str(me.get("id") or ""), "username": username, "display_name": display or "Moderator"}


def _request_query(extra):
    return f"receiver.community:{COMMUNITY_ID} AND type:community-submission AND {extra}"


def _comments(timeline):
    return [event for event in timeline if event.get("type") == "C"
            and isinstance(event.get("payload"), dict)
            and plain(event["payload"].get("content"))]


def _team_ids(current_user_id):
    return {value for value in (str(current_user_id or ""), HISTORICAL_MODERATOR_ID) if value}


def queue_row(request, timeline, current_user_id):
    comments = _comments(timeline)
    team = _team_ids(current_user_id)
    team_comments = [item for item in comments if str(item.get("created_by", {}).get("user")) in team]
    last = comments[-1] if comments else None
    last_id = str(last.get("created_by", {}).get("user")) if last else ""
    if not team_comments:
        bucket = "new"
    elif last_id not in team:
        bucket = "replied"
    else:
        bucket = "waiting"
    topic = request.get("topic") if isinstance(request.get("topic"), dict) else {}
    return {
        "request_id": str(request.get("id") or ""),
        "record_id": str(topic.get("record") or ""),
        "title": str(request.get("title") or "Untitled submission"),
        "status": str(request.get("status") or "unknown"),
        "created": request.get("created"),
        "updated": (last or {}).get("updated") or (last or {}).get("created") or request.get("updated"),
        "bucket": bucket,
        "comment_count": len(comments),
        "moderator_comment_count": len(team_comments),
        "last_actor": "team" if last and last_id in team else ("submitter" if last else "none"),
        "last_message": plain(last.get("payload", {}).get("content"))[:280] if last else "",
    }


def dashboard(archive):
    account = _identity(archive.me())
    hits = archive.requests(_request_query("is_open:true"), size=100)["hits"]
    rows = []
    timeline_errors = 0
    for request in hits:
        try:
            timeline = archive.timeline(str(request.get("id")))["hits"]
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, KeyError, TypeError):
            timeline = []
            timeline_errors += 1
        rows.append(queue_row(request, timeline, account["id"]))
    rows.sort(key=lambda item: item.get("updated") or "", reverse=True)
    accepted = archive.requests(_request_query("status:accepted"), size=1)["total"]
    declined = archive.requests(_request_query("status:declined"), size=1)["total"]
    counts = {bucket: sum(item["bucket"] == bucket for item in rows)
              for bucket in ("new", "replied", "waiting")}
    return {
        "account": account,
        "summary": {
            "open": len(rows),
            "new": counts["new"],
            "replies": counts["replied"],
            "waiting": counts["waiting"],
            "accepted_total": accepted,
            "declined_total": declined,
        },
        "requests": rows,
        "timeline_errors": timeline_errors,
        "refreshed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "access_mode": "comments_only",
    }


def history(archive, status="accepted", page=1, size=25):
    if status not in ("accepted", "declined", "submitted"):
        raise ValueError("Unsupported history status.")
    result = archive.requests(_request_query(f"status:{status}"), size=size, page=page)
    rows = []
    for request in result["hits"]:
        topic = request.get("topic") if isinstance(request.get("topic"), dict) else {}
        rows.append({
            "request_id": str(request.get("id") or ""),
            "record_id": str(topic.get("record") or ""),
            "title": str(request.get("title") or "Untitled submission"),
            "status": str(request.get("status") or "unknown"),
            "created": request.get("created"),
            "updated": request.get("updated"),
        })
    return {"status": status, "page": page, "size": size, "total": result["total"], "requests": rows}


def _section(key):
    if key in SECTION_BY_KEY:
        return SECTION_BY_KEY[key]
    for prefix in ("ref", "af", "rt", "mc", "t", "a", "d", "f", "k", "l"):
        if key.startswith(prefix):
            return SECTION_BY_KEY[prefix]
    return "Other"


def compose_reply(findings, playbook, follow_up=False):
    groups = {}
    seen = set()
    moderator_only = set(playbook.get("moderator_only_finding_keys", []))
    for finding in findings:
        key = str(finding.get("key") or "")
        text = str(finding.get("text") or "").strip()
        if text.startswith("- "):
            text = text[2:]
        if not text or key == "SIZE" or key in moderator_only or text in seen:
            continue
        seen.add(text)
        groups.setdefault(_section(key), []).append(text)
    if not groups:
        return ""
    templates = playbook["templates"]
    opening = templates["follow_up_opening"] if follow_up else templates["first_review_opening"]
    order = playbook["section_order"]
    sections = []
    for name in order:
        if name in groups:
            sections.append(name + "\n" + "\n".join("- " + item for item in groups[name]))
    for name in sorted(set(groups) - set(order)):
        sections.append(name + "\n" + "\n".join("- " + item for item in groups[name]))
    return opening + "\n\n" + "\n\n".join(sections) + "\n\n" + templates["changes_closing"]


def detail(archive, request_id, verdict_builder, playbook):
    request = archive.request(request_id)
    receiver = request.get("receiver") if isinstance(request.get("receiver"), dict) else {}
    if str(receiver.get("community")) != COMMUNITY_ID:
        raise ValueError("This request does not belong to the Materials Cloud Archive community.")
    topic = request.get("topic") if isinstance(request.get("topic"), dict) else {}
    record_id = str(topic.get("record") or "")
    if not record_id:
        raise ValueError("This request has no record topic.")
    draft = request.get("is_open") is True and request.get("status") == "submitted"
    try:
        record = archive.record(record_id, draft=draft)
    except urllib.error.HTTPError as error:
        if not draft and error.code == 404:
            record = archive.record(record_id, draft=False)
        else:
            raise
    timeline = archive.timeline(request_id)["hits"]
    me = _identity(archive.me())
    team = _team_ids(me["id"])
    comments = _comments(timeline)
    moderator_seen = False
    follow_up = False
    conversation = []
    for event in comments:
        author_id = str(event.get("created_by", {}).get("user") or "")
        is_team = author_id in team
        if is_team:
            moderator_seen = True
        elif moderator_seen:
            follow_up = True
        conversation.append({
            "id": str(event.get("id") or ""),
            "created": event.get("created"),
            "updated": event.get("updated"),
            "author": ("You" if author_id == me["id"] else
                       "Francesca" if author_id == HISTORICAL_MODERATOR_ID else
                       "Submitter"),
            "role": "moderator" if is_team else "submitter",
            "content": plain(event.get("payload", {}).get("content")),
        })
    result = verdict_builder(record)
    moderator_only = set(playbook.get("moderator_only_finding_keys", []))
    for finding in result["findings"]:
        finding["handling"] = "moderator_edit" if finding["key"] in moderator_only else "author"
    result["reply_draft"] = compose_reply(result["findings"], playbook, follow_up=follow_up)
    result["request"] = {
        "id": request_id,
        "status": request.get("status"),
        "is_open": request.get("is_open") is True,
        "created": request.get("created"),
        "updated": request.get("updated"),
        "revision_id": request.get("revision_id"),
        "url": archive.base + f"/communities/{COMMUNITY_SLUG}/requests/{request_id}",
        "record_url": archive.base + (f"/uploads/{record_id}" if draft else f"/records/{record_id}"),
    }
    result["context"] = {
        "kind": "request",
        "request_id": request_id,
        "record_id": record_id,
        "request_status": request.get("status"),
        "request_type": request.get("type"),
        "request_updated": request.get("updated"),
        "is_open": request.get("is_open") is True,
        "draft": draft,
        "label": request_id,
        "url": result["request"]["url"],
    }
    result["record"] = record
    result["conversation"] = conversation
    result["queue_bucket"] = queue_row(request, timeline, me["id"])["bucket"]
    result["_timeline_fingerprint"] = timeline_fingerprint(timeline)
    result["follow_up"] = follow_up
    result["account"] = me
    return result
