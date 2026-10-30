#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
[ -f schedule.json ] || cp schedule.example.json schedule.json
echo "1) edit .env   2) python -m callme login   3) python -m callme serve"
