#!/usr/bin/env bash
# Render build step. Make executable: chmod +x build.sh
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
python manage.py seed_plans
