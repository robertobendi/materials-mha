#!/usr/bin/env python3
"""Submit a test record to the mock archive.

    python3 scripts/submit_test_record.py --list
    python3 scripts/submit_test_record.py clean
    python3 scripts/submit_test_record.py bad_title no_readme
    python3 scripts/submit_test_record.py --all
    python3 scripts/submit_test_record.py --file my_record.json

Refuses to run against anything but a localhost MC_API_BASE.
"""
import argparse, copy, json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("MC_API_BASE", "http://127.0.0.1:8899")
CLEAN = json.load(open(os.path.join(HERE, "..", "fixtures", "base_clean.json"), encoding="utf-8"))


def mut(fn):
    r = copy.deepcopy(CLEAN)
    fn(r)
    return r


def _title(r, t): r["metadata"]["title"] = t
def _files(r, d): r["files"]["entries"] = d; r["files"]["count"] = len(d)


PRESETS = {
    "clean": lambda: copy.deepcopy(CLEAN),

    "bad_title": lambda: mut(lambda r: _title(
        r, "Machine-Learned Interatomic Potentials For Lithium Argyrodites From First Principles")),

    "title_dataset_word": lambda: mut(lambda r: _title(r, "PYM dataset")),

    "no_readme": lambda: mut(lambda r: _files(r, {
        "training_set.tar.gz": {"size": 812340000, "mimetype": "application/gzip", "ext": "gz"},
        "trajectories.h5": {"size": 2140000000, "mimetype": "application/x-hdf5", "ext": "h5"}})),

    "readme_no_ext": lambda: mut(lambda r: _files(r, {
        "README": {"size": 4102, "mimetype": "text/plain", "ext": ""},
        "data.csv": {"size": 51200, "mimetype": "text/csv", "ext": "csv"}})),

    "closed_format": lambda: mut(lambda r: _files(r, {
        "README.md": {"size": 4102, "mimetype": "text/markdown", "ext": "md"},
        "results.rar": {"size": 91200000, "mimetype": "application/x-rar", "ext": "rar"},
        "analysis.mat": {"size": 3400000, "mimetype": "application/octet-stream", "ext": "mat"}})),

    "too_big": lambda: mut(lambda r: _files(r, {
        "README.md": {"size": 4102, "mimetype": "text/markdown", "ext": "md"},
        "huge.tar.gz": {"size": 31000000000, "mimetype": "application/gzip", "ext": "gz"}})),

    "too_big_no_waiver": lambda: mut(lambda r: (
        _files(r, {"README.md": {"size": 4102, "mimetype": "text/markdown", "ext": "md"},
                   "huge.tar.gz": {"size": 31000000000, "mimetype": "application/gzip", "ext": "gz"}}),
        r["metadata"].update(funding=[]))),

    "aiida_big_ok": lambda: mut(lambda r: _files(r, {
        "README.md": {"size": 4102, "mimetype": "text/markdown", "ext": "md"},
        "export.aiida": {"size": 22000000000, "mimetype": "application/octet-stream", "ext": "aiida"}})),

    "doi_with_https": lambda: mut(lambda r: r["custom_fields"]["mc_references"][0].update(
        ref_citation="M. Rossi et al., PRM 10, 015401 (2026), https://doi.org/10.1103/PhysRevMaterials.10.015401")),

    "no_reference": lambda: mut(lambda r: r["custom_fields"].update(mc_references=[])),

    "thin_reference": lambda: mut(lambda r: r["custom_fields"]["mc_references"][0].update(
        ref_citation="Author, Title, Journal, 2025")),

    "preprint_no_link": lambda: mut(lambda r: r["custom_fields"]["mc_references"][0].update(
        ref_citation="A. Author, B. Author, A computational materials study, arXiv preprint (2026)")),

    "bad_affiliation": lambda: mut(lambda r: r["metadata"]["creators"][1].update(
        affiliations=[{"name": "1. Grunberg Institute"}])),

    "no_affiliation": lambda: mut(lambda r: r["metadata"]["creators"][1].pop("affiliations", None)),

    "bad_description": lambda: mut(lambda r: r["metadata"].update(
        description="<p>this folder contains md trajectories in the traj/ dir and the potential in pot/</p>")),

    "few_keywords": lambda: mut(lambda r: r["metadata"].update(subjects=[{"subject": "DFT"}])),

    "experimental": lambda: mut(lambda r: (
        _title(r, "Atomic force microscopy of pentacene monolayers on Cu(111)"),
        r["metadata"].update(
            description="<p>We report AFM images of pentacene monolayers grown on copper substrates.</p>",
            funding=[]),
        _files(r, {"scan01.sxm": {"size": 2400000, "mimetype": "application/octet-stream", "ext": "sxm"},
                   "scan02.sxm": {"size": 2400000, "mimetype": "application/octet-stream", "ext": "sxm"}}))),

    "pdf_paper": lambda: mut(lambda r: _files(r, {
        "README.md": {"size": 4102, "mimetype": "text/markdown", "ext": "md"},
        "manuscript.pdf": {"size": 1800000, "mimetype": "application/pdf", "ext": "pdf"},
        "data.csv": {"size": 51200, "mimetype": "text/csv", "ext": "csv"}})),

    "kitchen_sink": lambda: mut(lambda r: (
        _title(r, "PYM Dataset Of Structures"),
        r["metadata"].update(description="<p>files are here</p>", subjects=[], funding=[]),
        r["metadata"]["creators"][1].update(affiliations=[{"name": "2. Aachen University"}]),
        r["custom_fields"].update(mc_references=[]),
        _files(r, {"data.rar": {"size": 12000000, "mimetype": "application/x-rar", "ext": "rar"}}))),
}


def submit(rec, label):
    req = urllib.request.Request(BASE.rstrip("/") + "/api/mock/submit",
                                 data=json.dumps(rec).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        out = json.load(r)
    print(f"  {label:22s} record {out['record_id']}  request {out['request_id']}")
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("presets", nargs="*")
    p.add_argument("--list", action="store_true")
    p.add_argument("--all", action="store_true")
    p.add_argument("--file")
    a = p.parse_args()

    if a.list:
        for k in PRESETS:
            print(k)
        sys.exit(0)

    host = BASE.split("//")[-1].split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        sys.exit(f"refusing to submit test records to {BASE}; point MC_API_BASE at the mock.")

    if a.file:
        submit(json.load(open(a.file, encoding="utf-8")), os.path.basename(a.file))
    names = list(PRESETS) if a.all else a.presets
    for n in names:
        if n not in PRESETS:
            sys.exit(f"unknown preset {n!r}; try --list")
        submit(PRESETS[n](), n)
    if not names and not a.file:
        p.print_help()
