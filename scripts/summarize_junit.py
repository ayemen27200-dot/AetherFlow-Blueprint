#!/usr/bin/env python3
"""Render a JUnit XML report as a short Markdown summary.

Prints the summary to stdout and, when ``GITHUB_STEP_SUMMARY`` is set (CI),
appends it to the job summary so exact test counts and skipped-case names
are visible in the Actions UI without downloading any artifact.
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET


def build_summary(xml_path: str) -> str:
    root = ET.parse(xml_path).getroot()
    suite = root.find("testsuite")
    if suite is None:
        raise SystemExit(f"no <testsuite> element found in {xml_path}")

    lines = [
        "## Test results",
        f"- tests: **{suite.get('tests')}**",
        f"- failures: **{suite.get('failures')}**",
        f"- errors: **{suite.get('errors')}**",
        f"- skipped: **{suite.get('skipped')}**",
    ]
    skipped_cases = [
        tc for tc in suite.iter("testcase") if tc.find("skipped") is not None
    ]
    if skipped_cases:
        lines.append("")
        lines.append("### Skipped cases")
        lines.extend(
            f"- `{tc.get('classname')}::{tc.get('name')}`" for tc in skipped_cases
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: summarize_junit.py <junit-xml-path>")
    summary = build_summary(sys.argv[1])
    sys.stdout.write(summary)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(summary)


if __name__ == "__main__":
    main()
