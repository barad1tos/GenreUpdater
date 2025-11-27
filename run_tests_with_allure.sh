#!/bin/bash

# Music Genre Updater v2.0 - Test Runner with Allure Dashboard
# Usage: ./run_tests_with_allure.sh [test_pattern]

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "🧪 Running tests with Allure reporting..."

# Run tests with Allure results generation
if [ -z "$1" ]; then
	echo "Running all tests..."
	uv run pytest -v
else
	echo "Running tests matching pattern: $1"
	uv run pytest "$1" -v
fi

echo ""
echo "📊 Generated reports:"
echo "  HTML Report: file://$PROJECT_DIR/reports/html/report.html"
echo "  Coverage Report: file://$PROJECT_DIR/reports/html/coverage/index.html"
echo ""
echo "🚀 Starting Allure dashboard..."
echo "  Dashboard URL: http://127.0.0.1:8080"
echo "  Press Ctrl+C to stop the server"
echo ""

# Start Allure server (use full path to avoid alias conflicts)
/opt/homebrew/bin/allure serve reports/allure-results --port 8080
