#!/usr/bin/env python3
"""Pure rule checks. No LLM. Each finding carries a canned.json key."""
import html
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(HERE, "..", "rules")
FMT = json.load(open(os.path.join(RULES, "formats.json"), encoding="utf-8"))
CANNED = json.load(open(os.path.join(RULES, "canned.json"), encoding="utf-8"))

DOI_RE = re.compile(r"(?<![\w.])10\.\d{4,9}/\S+", re.I)
URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
PREPRINT_RE = re.compile(r"\b(?:pre[\s-]?print|arxiv)\b", re.I)
REFERENCE_LINK_FIELDS = ("ref_link", "ref_doi", "doi", "url")


def canned(key):
    for sec in CANNED.values():
        if key in sec:
            return sec[key]
    return f"[missing canned response {key}]"


def ext_of(name):
    n = name.lower()
    for e in (".tar.gz", ".tar.bz2"):
        if n.endswith(e):
            return e
    return os.path.splitext(n)[1]


def affiliation_name(affiliation):
    return " ".join((affiliation.get("name") or "").split()).lower()


def affiliation_ror_index(creators):
    known = {}
    for creator in creators:
        for affiliation in creator.get("affiliations") or []:
            name, identifier = affiliation_name(affiliation), affiliation.get("id")
            if name and isinstance(identifier, str) and identifier.strip():
                known.setdefault(name, set()).add(identifier.strip())
    return known


def affiliation_rors(creators):
    known = affiliation_ror_index(creators)

    resolved = []
    for creator in creators:
        row = []
        for affiliation in creator.get("affiliations") or []:
            candidates = known.get(affiliation_name(affiliation), set())
            identifier = affiliation.get("id")
            identifier = identifier.strip() if isinstance(identifier, str) else ""
            # the same institution string can show up under several creators
            row.append(identifier or
                       (next(iter(candidates)) if len(candidates) == 1 else None))
        resolved.append(row)
    return resolved


def is_preprint_without_link(reference):
    citation = reference.get("ref_citation", "")
    resource_type = str(reference.get("ref_resource_type", ""))
    is_preprint = PREPRINT_RE.search(citation) or "preprint" in resource_type.lower()
    structured = " ".join(str(reference.get(key) or "")
                          for key in REFERENCE_LINK_FIELDS)
    has_link = DOI_RE.search(citation) or URL_RE.search(citation) or \
        DOI_RE.search(structured) or URL_RE.search(structured)
    return bool(is_preprint and not has_link)


def is_thin_reference(reference):
    """Look at all reference fields, not just the display citation."""
    citation = str(reference.get("ref_citation") or "")
    comment = str(reference.get("ref_comment") or "")
    links = " ".join(str(reference.get(key) or "") for key in REFERENCE_LINK_FIELDS)
    if DOI_RE.search(citation) or URL_RE.search(citation) or \
            DOI_RE.search(links) or URL_RE.search(links):
        return False
    return len((citation + " " + comment).split()) < 5


def check(rec, entries):
    md = rec["metadata"]
    f = []

    def add(key, detail=None):
        f.append({"key": key, "text": canned(key), "detail": detail})

    # title
    title = md.get("title", "")
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", title)
    titlecase = len(words) > 3 and sum(1 for w in words[1:] if w[:1].isupper()) >= max(2, int(0.6 * (len(words) - 1)))
    if titlecase:
        add("t1", f"title looks title-cased: {title!r}")
    if re.search(r"\bdata ?set\b", title, re.I):
        add("t2", title)
    if len(words) < 4:
        add("t3", f"title too short/acronym-like: {title!r}")

    # affiliations
    rors = affiliation_rors(md.get("creators", []))
    known_rors = affiliation_ror_index(md.get("creators", []))
    address_findings, conflicts = {}, set()
    for creator_index, c in enumerate(md.get("creators", [])):
        affs = c.get("affiliations") or []
        name = c["person_or_org"].get("name", "?")
        if not affs:
            add("af3", f"no affiliation for {name}")
            continue
        for affiliation_index, a in enumerate(affs):
            institution = affiliation_name(a)
            if len(known_rors.get(institution, set())) > 1:
                if institution not in conflicts:
                    conflicts.add(institution)
                    f.append({"key": "REVIEW", "text": "Human check: conflicting ROR identifiers for "
                              + a.get("name", "") + ". Verify the institution before requesting affiliation changes.",
                              "detail": institution})
                continue
            if rors[creator_index][affiliation_index]:
                continue  # ROR-resolved
            txt = a.get("name", "")
            if re.match(r"^\s*\d+[.)]", txt):
                add("af1", txt)
            if txt.count(",") < 2:
                if institution not in address_findings:
                    address_findings[institution] = {"name": txt, "authors": []}
                if name not in address_findings[institution]["authors"]:
                    address_findings[institution]["authors"].append(name)

    for item in address_findings.values():
        add("af2", f"{', '.join(item['authors'])}: {item['name']!r} - no ROR supplied or shared; "
            "check whether the affiliation includes a full address")

    # description
    desc = html.unescape(re.sub(r"<[^>]+>", " ", md.get("description", "")))
    desc = re.sub(r"\s+", " ", desc).strip()
    if not desc:
        add("d3", "no description")
    else:
        if desc[:1].islower():
            add("d7", desc[:60])
        if not desc.rstrip().endswith("."):
            add("d8", desc[-60:])
        if len(desc.split()) < 60:
            add("d6", f"{len(desc.split())} words")

    # keywords
    subs = md.get("subjects", [])
    if len(subs) < 3:
        add("k1", f"{len(subs)} keywords")

    # resource type
    rt = md.get("resource_type", {}).get("id")
    if rt == "software" and not any(ext_of(e["key"]) in (".py", ".sh", ".ipynb") for e in entries):
        archives = [e["key"] for e in entries
                    if ext_of(e["key"]) in (".zip", ".tar.gz", ".tar.bz2")]
        if archives:
            f.append({"key": "REVIEW", "text": "Human check: Software is selected and the code may "
                      "be inside an archive. Inspect the README/archive before requesting a resource-type change.",
                      "detail": ", ".join(archives)})
        else:
            add("rt1", "resource_type=software but no visible code files or archives")

    # references
    refs = rec.get("custom_fields", {}).get("mc_references", [])
    if not refs:
        add("ref2", "no reference at all")
    for r in refs:
        cit = r.get("ref_citation", "")
        if "doi.org" in cit or re.search(r"https?://(dx\.)?doi\.org/", cit):
            add("ref4", cit)
        if is_preprint_without_link(r):
            add("ref7", f"preprint reference has no DOI or URL: {cit!r}")
        if is_thin_reference(r):
            add("ref2", f"reference too thin: {cit!r}")
    doi = rec.get("pids", {}).get("doi", {}).get("identifier", "")
    if doi and not doi.startswith("10."):
        add("ref4", f"record DOI not in 10. form: {doi}")

    # files
    names = [e["key"] for e in entries]
    lower = [n.lower() for n in names]
    readme = [n for n in lower if n in FMT["readme_names"] or n.startswith("readme")]
    if not readme:
        add("f1", "no README.md/.txt or files_description.md")
    else:
        r0 = readme[0]
        if os.path.splitext(r0)[1] not in FMT["readme_preview_exts"]:
            add("f8", f"{r0} has no previewable extension")

    is_aiida = any(ext_of(n) in FMT["aiida_markers"] for n in names)
    budget = FMT["max_bytes_aiida"] if is_aiida else FMT["max_bytes"]
    total = sum(e.get("size", 0) for e in entries)
    if total > budget:
        f.append({"key": "SIZE", "text": f"Total size {total/1e9:.1f} GB exceeds the "
                  f"{budget/1e9:.0f} GB limit ({'AiiDA' if is_aiida else 'standard'}).",
                  "detail": f"{total} bytes"})

    for n in names:
        e = ext_of(n)
        if e in FMT["blocked"]:
            f.append({"key": "f7", "text": canned("f7"), "detail": f"{n}: {FMT['blocked'][e]}"})
        elif e in FMT["case_by_case"]:
            f.append({"key": "REVIEW", "text": f"Human check: {n} - {FMT['case_by_case'][e]}", "detail": n})
        elif e == ".pdf":
            f.append({"key": "REVIEW", "text": f"Human check: {n} - confirm it does not reproduce text "
                      "from an unpublished paper.", "detail": n})
        elif e and e not in FMT["open"] and e not in FMT["tolerated"]:
            f.append({"key": "REVIEW", "text": f"Human check: unknown extension {e} ({n}).", "detail": n})

    # funding waiver
    funders = []
    for x in md.get("funding", []):
        name = x.get("funder", {}).get("name", "").strip()
        acr = (x.get("award") or {}).get("acronym") or ""
        funders.append(f"{name} ({acr})" if acr and acr not in name else name)
    funding_text = " ".join(funders)
    waived = any(re.search(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])",
                           funding_text, re.I) for w in FMT["waiver_funders"])

    return {"findings": f, "total_bytes": total, "is_aiida": is_aiida,
            "funders": funders, "funding_waiver": waived, "affiliation_rors": rors}


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    import mc_api
    rid = sys.argv[1]
    rec = mc_api.draft(rid)
    out = check(rec, mc_api.files(rid))
    print(json.dumps(out, indent=2, ensure_ascii=False))
