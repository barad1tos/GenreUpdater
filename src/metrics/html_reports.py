"""HTML Analytics Report Generation.

This module handles HTML report generation for analytics data,
including performance metrics, function call summaries, and dry-run reports.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.logger import ensure_directory, get_full_log_path


# Constant for duration field name (shared with analytics module)
DURATION_FIELD = "Duration (s)"


def _generate_empty_html_template(
    date_str: str,
    report_file: str,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> None:
    """Generate and save an empty HTML template when no data is available."""
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Analytics Report for {date_str}</title>
    <style>
        table {{
            border-collapse: collapse;
            width: 100%;
            font-size: 0.95em;
        }}
        th, td {{
            border: 1px solid #dddddd;
            text-align: left;
            padding: 6px;
        }}
        th {{
            background-color: #f2f2f2;
        }}
        .error {{
            background-color: #ffcccc;
        }}
    </style>
</head>
<body>
    <h2>Analytics Report for {date_str}</h2>
    <p><strong>No analytics data was collected during this run.</strong></p>
    <p>Possible reasons:</p>
    <ul>
        <li>Script executed in dry-run mode without analytics collection</li>
        <li>No decorated functions were called</li>
        <li>Decorator failed to log events</li>
    </ul>
</body>
</html>"""
    try:
        Path(report_file).parent.mkdir(parents=True, exist_ok=True)
        with Path(report_file).open("w", encoding="utf-8") as file:
            file.write(html_content)
        console_logger.info("Empty analytics HTML report saved to %s.", report_file)
    except (OSError, UnicodeError):
        error_logger.exception("Failed to save empty HTML report")


def _group_events_by_duration_and_success(
    events: list[dict[str, Any]],
    duration_thresholds: dict[str, float],
    group_successful_short_calls: bool,
    error_logger: logging.Logger,
) -> tuple[dict[tuple[str, str], dict[str, float]], list[dict[str, Any]]]:
    """Group events by duration and success status."""
    grouped_short_success: dict[tuple[str, str], dict[str, float]] = {}
    big_or_fail_events: list[dict[str, Any]] = []
    short_max = duration_thresholds.get("short_max", 2)

    if not group_successful_short_calls:
        return grouped_short_success, events

    for event in events:
        try:
            event_duration = event[DURATION_FIELD]
            success = event["Success"]

            # Validate duration is numeric
            if not isinstance(event_duration, int | float):
                error_logger.warning(
                    "Invalid duration type in event (expected number, got %s): %s",
                    type(event_duration).__name__,
                    event,
                )
                big_or_fail_events.append(event)
                continue

            if success and event_duration <= short_max:
                key = (
                    event.get("Function", "Unknown"),
                    event.get("Event Type", "Unknown"),
                )
                if key not in grouped_short_success:
                    grouped_short_success[key] = {"count": 0, "total_duration": 0.0}
                grouped_short_success[key]["count"] += 1
                grouped_short_success[key]["total_duration"] += event_duration
            else:
                big_or_fail_events.append(event)
        except KeyError:
            error_logger.exception(
                "Missing key in event data during grouping, event: %s",
                event,
            )
            big_or_fail_events.append(event)

    return grouped_short_success, big_or_fail_events


def _generate_main_html_template(
    date_str: str,
    call_counts: dict[str, int],
    success_counts: dict[str, int],
    events: list[dict[str, Any]],
    force_mode: bool,
) -> str:
    """Generate the main HTML template with header and summary."""
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Analytics Report for {date_str}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            line-height: 1.6;
        }}
        h2, h3 {{
            color: #333;
            border-bottom: 1px solid #ddd;
            padding-bottom: 10px;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            font-size: 0.95em;
            margin-bottom: 20px;
        }}
        th, td {{
            border: 1px solid #dddddd;
            text-align: left;
            padding: 8px;
        }}
        th {{
            background-color: #f2f2f2;
            position: sticky;
            top: 0;
        }}
        tr:nth-child(even) {{
            background-color: #f9f9f9;
        }}
        .error {{
            background-color: #ffcccc;
        }}
        .summary {{
            background-color: #e6f3ff;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
        }}
        .run-type {{
            font-weight: bold;
            color: #0066cc;
        }}
        .duration-short {{ background-color: #e0ffe0; }}
        .duration-medium {{ background-color: #fffacd; }}
        .duration-long {{ background-color: #ffb0b0; }}
    </style>
</head>
<body>
    <h2>Analytics Report for {date_str}</h2>
    <div class="summary">
        <p class="run-type">Run type: {"Full scan" if force_mode else "Incremental update"}</p>
        <p><strong>Total functions:</strong> {len(call_counts)}</p>
        <p><strong>Total events:</strong> {len(events)}</p>
        <p><strong>Success rate:</strong> {
        (sum(success_counts.values()) / sum(call_counts.values()) * 100 if sum(call_counts.values()) else 0):.1f}%</p>
    </div>"""


def _generate_grouped_success_table(
    grouped_short_success: dict[tuple[str, str], dict[str, float]],
    group_successful_short_calls: bool,
) -> str:
    """Generate HTML table for grouped successful short calls."""
    html = """
    <h3>Grouped Short & Successful Calls</h3>
    <table>
        <tr>
            <th>Function</th>
            <th>Event Type</th>
            <th>Count</th>
            <th>Avg Duration (s)</th>
            <th>Total Duration (s)</th>
        </tr>"""

    if not (group_successful_short_calls and grouped_short_success):
        html += """
        <tr><td colspan="5">No short successful calls found or grouping disabled.</td></tr>"""
    else:
        for (function_name, event_type), values in sorted(grouped_short_success.items()):
            count = values["count"]
            total_duration = values["total_duration"]
            avg_duration = round(total_duration / count, 4) if count > 0 else 0
            html += f"""
        <tr>
            <td>{function_name}</td>
            <td>{event_type}</td>
            <td>{count}</td>
            <td>{avg_duration}</td>
            <td>{round(total_duration, 4)}</td>
        </tr>"""

    html += "</table>"
    return html


def _get_duration_category(
    event_duration: float,
    duration_thresholds: dict[str, float],
) -> str:
    """Determine the duration category based on thresholds."""
    if event_duration <= duration_thresholds.get("short_max", 2):
        return "short"
    if event_duration <= duration_thresholds.get("medium_max", 5):
        return "medium"
    return "long"


def _determine_event_row_class(
    event: dict[str, Any],
    duration_thresholds: dict[str, float],
) -> str:
    """Determine the CSS class for an event table row based on success and duration."""
    success = event.get("Success", False)
    if not success:
        return "error"

    event_duration = event.get(DURATION_FIELD, 0)
    duration_category = _get_duration_category(event_duration, duration_thresholds)
    return f"duration-{duration_category}"


def _format_event_table_row(event: dict[str, Any], row_class: str) -> str:
    """Format a single event as an HTML table row."""
    event_duration = event.get(DURATION_FIELD, 0)
    success = event.get("Success", False)
    success_display = "Yes" if success else "No"

    return f"""
        <tr class="{row_class}">
            <td>{event.get("Function", "Unknown")}</td>
            <td>{event.get("Event Type", "Unknown")}</td>
            <td>{event.get("Start Time", "Unknown")}</td>
            <td>{event.get("End Time", "Unknown")}</td>
            <td>{event_duration}</td>
            <td>{success_display}</td>
        </tr>"""


def _generate_detailed_events_table_html(
    big_or_fail_events: list[dict[str, Any]],
    duration_thresholds: dict[str, float],
    error_logger: logging.Logger,
) -> str:
    """Generate HTML table for detailed events (errors or long/medium calls)."""
    html = """
    <h3>Detailed Calls (Errors or Long/Medium Calls)</h3>
    <table>
        <tr>
            <th>Function</th>
            <th>Event Type</th>
            <th>Start Time</th>
            <th>End Time</th>
            <th>Duration (s)</th>
            <th>Success</th>
        </tr>"""

    def _safe_start_time(event_record: dict[str, Any]) -> str:
        """Extract sortable start time string, ensuring string type."""
        start_time = event_record.get("Start Time", "")
        return start_time if isinstance(start_time, str) else ""

    if big_or_fail_events:
        for event in sorted(big_or_fail_events, key=_safe_start_time):
            try:
                row_class = _determine_event_row_class(event, duration_thresholds)
                html += _format_event_table_row(event, row_class)
            except KeyError:
                error_logger.exception(
                    "Error formatting event for detailed list, event data: %s",
                    event,
                )
    else:
        html += """
        <tr><td colspan="6">No detailed calls to display.</td></tr>"""

    html += "</table>"
    return html


def _generate_summary_table_html(
    call_counts: dict[str, int],
    success_counts: dict[str, int],
    decorator_overhead: dict[str, float],
) -> str:
    """Generate HTML table for function call summary."""
    html = """
    <h3>Summary</h3>
    <table>
        <tr>
            <th>Function</th>
            <th>Call Count</th>
            <th>Success Count</th>
            <th>Success Rate (%)</th>
            <th>Total Decorator Overhead (s)</th>
        </tr>"""

    if call_counts:
        for function_name, count in sorted(call_counts.items()):
            success_count = success_counts.get(function_name, 0)
            success_rate = (success_count / count * 100) if count else 0
            overhead = decorator_overhead.get(function_name, 0)

            html += f"""
        <tr>
            <td>{function_name}</td>
            <td>{count}</td>
            <td>{success_count}</td>
            <td>{success_rate:.2f}</td>
            <td>{round(overhead, 4)}</td>
        </tr>"""
    else:
        html += """
        <tr><td colspan="5">No function calls recorded.</td></tr>"""

    html += """
    </table>
</body>
</html>"""
    return html


def save_html_report(
    events: list[dict[str, Any]],
    call_counts: dict[str, int],
    success_counts: dict[str, int],
    decorator_overhead: dict[str, float],
    config: dict[str, Any],
    console_logger: logging.Logger | None = None,
    error_logger: logging.Logger | None = None,
    group_successful_short_calls: bool = False,
    force_mode: bool = False,
) -> None:
    """Generate an HTML report from the provided analytics data."""
    if console_logger is None:
        console_logger = logging.getLogger("console_logger")
    if error_logger is None:
        error_logger = logging.getLogger("error_logger")

    # Configuration and setup
    console_logger.info(
        "Starting HTML report generation with %d events, %d function counts",
        len(events),
        len(call_counts),
    )
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    logs_base_dir = config.get("logs_base_dir", "")
    reports_dir = Path(logs_base_dir) / "analytics"
    reports_dir.mkdir(parents=True, exist_ok=True)

    report_file = get_full_log_path(
        config,
        "analytics_html_report_file",
        str(Path("analytics") / ("analytics_full.html" if force_mode else "analytics_incremental.html")),
    )
    duration_thresholds = config.get("analytics", {}).get(
        "duration_thresholds",
        {"short_max": 2, "medium_max": 5, "long_max": 10},
    )

    # Check for empty data
    if not events and not call_counts:
        console_logger.warning(
            "No analytics data available for report - creating empty template",
        )
        _generate_empty_html_template(date_str, report_file, console_logger, error_logger)
        return

    # Group events
    grouped_short_success, big_or_fail_events = _group_events_by_duration_and_success(
        events, duration_thresholds, group_successful_short_calls, error_logger
    )

    # Generate HTML sections
    html_content = _generate_main_html_template(date_str, call_counts, success_counts, events, force_mode)
    html_content += _generate_grouped_success_table(grouped_short_success, group_successful_short_calls)
    html_content += _generate_detailed_events_table_html(big_or_fail_events, duration_thresholds, error_logger)
    html_content += _generate_summary_table_html(call_counts, success_counts, decorator_overhead)

    # Save the report
    try:
        Path(report_file).parent.mkdir(parents=True, exist_ok=True)
        with Path(report_file).open("w", encoding="utf-8") as file:
            file.write(html_content)
        console_logger.info("Analytics HTML report saved to %s.", report_file)
    except (OSError, UnicodeError):
        error_logger.exception("Failed to save HTML report")


def save_detailed_dry_run_report(
    changes: list[dict[str, str]],
    file_path: str,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> None:
    """Generate a detailed HTML report with separate tables for each change type."""
    if not changes:
        console_logger.info("No changes to report for dry run.")
        return

    # Group changes by type
    changes_by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
    for change in changes:
        change_type = change.get("change_type", "unknown").replace("_", " ").title()
        changes_by_type[change_type].append(change)

    # Generate HTML
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Dry Run Report</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                margin: 10px;
                background-color: #f9f9f9;
                color: #333;
            }
            h2 { color: #1a1a1a; border-bottom: 2px solid #ddd; padding-bottom: 5px; }
            table {
                border-collapse: collapse; width: 100%; margin-bottom: 15px;
                box-shadow: 0 2px 5px rgba(0,0,0,0.1); table-layout: auto;
            }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; white-space: nowrap; }
            thead { background-color: #e9ecef; }
            th { font-weight: 600; }
            tbody tr:nth-child(even) { background-color: #f2f2f2; }
            tbody tr:hover { background-color: #e9e9e9; }
            .container { max-width: 1200px; margin: auto; background: white; padding: 20px; border-radius: 8px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Dry Run Simulation Report</h1>
    """

    # Create table for each change type
    for change_type, change_list in changes_by_type.items():
        if not change_list:
            continue

        html += f"<h2>{change_type} ({len(change_list)} potential changes)</h2>"

        # Strictly defined columns for each report type for reliability
        header_map = {
            "Cleaning": [
                "artist",
                "original_name",
                "cleaned_name",
                "original_album",
                "cleaned_album",
            ],
            "Genre Update": [
                "artist",
                "album",
                "track_name",
                "original_genre",
                "new_genre",
            ],
            "Year Update": [
                "artist",
                "album",
                "track_name",
                "original_year",
                "simulated_year",
            ],
        }

        # Get the correct list of keys for the current report type.
        # If the type is unknown, fallback to the old behavior as a backup option.
        headers = header_map.get(
            change_type,
            [h for h in change_list[0] if h not in ["change_type", "timestamp", "track_id", "date_added"]],
        )

        html += "<table><thead><tr>"
        for header in headers:
            # Create readable column headers
            html += f"<th>{header.replace('_', ' ').title()}</th>"
        html += "</tr></thead><tbody>"

        # Fill table with data
        for item in change_list:
            html += "<tr>"
            # Go through the fixed list of headers
            for header_key in headers:
                value = item.get(header_key, "")
                html += f"<td>{value}</td>"
            html += "</tr>"

        html += "</tbody></table>"

    html += """
        </div>
    </body>
    </html>
    """

    # Save HTML file
    try:
        ensure_directory(str(Path(file_path).parent))
        with Path(file_path).open("w", encoding="utf-8") as f:
            f.write(html)
        console_logger.info(
            "Successfully generated detailed dry run HTML report at: %s",
            file_path,
        )
    except (OSError, UnicodeError):
        error_logger.exception("Failed to save detailed dry run HTML report")
