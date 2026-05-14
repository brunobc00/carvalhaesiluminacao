#!/bin/bash
set -e
cd /project
git pull origin main
docker compose up --build -d
