#!/usr/bin/env bash
set -e
pip install -r requirements.txt
playwright install chromium
sudo apt-get update
sudo apt-get install -y libatk1.0-0 libatk-bridge2.0-0
playwright install-deps chromium
echo "Environnement Playwright prêt."