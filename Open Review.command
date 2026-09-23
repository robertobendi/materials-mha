#!/bin/zsh
set -eu
cd "${0:A:h}"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
exec .venv/bin/python app/serve.py --open
