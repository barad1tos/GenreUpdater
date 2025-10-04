#!/bin/bash
# Pre-commit hook for checking critical function parameters
# Usage: ./scripts/check_critical_params.sh or add to .git/hooks/pre-commit

set -e

echo "🔍 Checking critical function parameters..."

# ERROR COUNTER
ERRORS=0

# Function to check the call
check_function_calls() {
	local func_name="$1"
	local required_param="$2"
	local src_dir="${3:-src/}"

	echo "  Checking $func_name requires $required_param..."

	# Find all files with function calls
	local files
	files=$(grep -rl "await $func_name\|$func_name(" "$src_dir" |
		grep -v ".pyc" |
		grep -v "__pycache__" || true)

	if [ -z "$files" ]; then
		echo "    ℹ️  No calls found (OK)"
		return 0
	fi

	# Check each file
	while IFS= read -r file; do
		# Get call locations with context (10 lines after)
		local calls_with_context
		calls_with_context=$(grep -n -A 10 "await $func_name\|$func_name(" "$file" |
			grep -v "def $func_name" || true)

		if [ -z "$calls_with_context" ]; then
			continue
		fi

		# Find call start lines
		local call_lines
		call_lines=$(echo "$calls_with_context" | grep "await $func_name\|$func_name(" | cut -d: -f1)

		# Check each call
		while IFS= read -r line_num; do
			[ -z "$line_num" ] && continue

			# Get context (next 10 lines from call)
			local end_line=$((line_num + 10))
			local context
			context=$(sed -n "${line_num},${end_line}p" "$file")

			# Check if parameter present in context
			if ! echo "$context" | grep -q "$required_param="; then
				echo "    ❌ ERROR: Missing $required_param:"
				echo "       $file:$line_num"
				ERRORS=$((ERRORS + 1))
			fi
		done <<<"$call_lines"
	done <<<"$files"

	if [ $ERRORS -eq 0 ]; then
		echo "    ✅ All calls include $required_param"
	fi
}

# === CRITICAL FUNCTIONS TO CHECK ===

# 1. sync_track_list_with_current must have an applescript_client
check_function_calls "sync_track_list_with_current" "applescript_client" "src/"

# 2. You can add other critical functions here
# check_function_calls "process_sensitive_data" "encryption_key" "src/"
# check_function_calls "execute_query" "sanitized" "src/"

# === RESULT ===

echo ""
if [ $ERRORS -gt 0 ]; then
	echo "❌ Found $ERRORS critical parameter issue(s)"
	echo ""
	echo "Fix by adding the required parameter to each call site."
	echo "See docs/BUG_PREVENTION_STRATEGY.md for details."
	exit 1
else
	echo "✅ All critical parameters present"
	exit 0
fi
