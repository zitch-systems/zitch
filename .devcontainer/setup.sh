#!/usr/bin/env bash
# Runs automatically when the Codespace / Dev Container is first created.
# Installs both halves of the project so you can run them immediately.
set -e

echo "============================================"
echo " Zitch dev environment setup"
echo "============================================"

# ---- Backend (Django) ----
echo ""
echo ">> Backend: installing Python dependencies"
cd backend
pip install --upgrade pip
pip install -r requirements.txt

# Seed a local .env from the example if one isn't present (SQLite, mock mode).
if [ ! -f .env ]; then
  cp .env.example .env
  echo ">> Created backend/.env from .env.example (SQLite + mock mode)"
fi

echo ">> Backend: applying migrations + seeding plans"
python manage.py migrate --no-input
python manage.py seed_plans
cd ..

# ---- Frontend (Expo) ----
echo ""
echo ">> Frontend: installing Node dependencies (this can take a few minutes)"
npm install

echo ""
echo "============================================"
echo " Setup complete."
echo ""
echo " Start the backend:"
echo "   cd backend && python manage.py runserver 0.0.0.0:8000"
echo ""
echo " Start the app (Metro):"
echo "   npx expo start"
echo ""
echo " Build an Android APK (needs your Expo login):"
echo "   npx eas-cli login && npx eas-cli init && npx eas-cli build -p android --profile preview"
echo "============================================"
