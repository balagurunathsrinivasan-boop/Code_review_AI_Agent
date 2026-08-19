from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from build_analyzer import XcodeBuildAnalyzer
from code_analyzer import CodeAnalyzer
from github_pr import GitHubPRClientV3
from review_models import FindingSource
from review_models import PRReviewResult
from review_models import Recommendation
from review_models import ReviewFinding
from review_models import Severity
from review_models import XcodeAnalysisResult


def load_env_file(
    env_path: Path | None = None,
) -> None:
    path = env_path or Path(
        os.getenv(
            "ENV_FILE",
            Path(__file__).resolve().with_name(".env"),
        )
    )

    if not path.exists():
        return

    for raw_line in path.read_text(
        encoding="utf-8",
    ).splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export ") :].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]

        os.environ.setdefault(key, value)


load_env_file()


GITHUB_OWNER = os.getenv(
    "GITHUB_OWNER",
    "",
).strip()
GITHUB_REPO = os.getenv(
    "GITHUB_REPO",
    "",
).strip()
PULL_REQUEST_NUMBER_ENV = os.getenv(
    "PULL_REQUEST_NUMBER",
    "",
).strip()
PULL_REQUEST_NUMBER = (
    int(PULL_REQUEST_NUMBER_ENV)
    if PULL_REQUEST_NUMBER_ENV
    else None
)
PR_LIST_STATE = os.getenv(
    "PR_LIST_STATE",
    "open",
).strip().lower() or "open"
V3_SCRIPT_VERSION = "2026-08-19.1"

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash-lite",
)

MAX_INLINE_COMMENTS = int(
    os.getenv(
        "MAX_INLINE_COMMENTS",
        "20",
    )
)
REVIEW_OUTPUT_DIR = os.getenv(
    "REVIEW_OUTPUT_DIR",
    "review_outputs",
).strip()

SUMMARY_MARKER = "pr-review-summary"
FINAL_COMMENT_MARKER = "pr-review-final-comment"
INLINE_MARKER_PREFIX = "pr-review-inline"

LEGACY_SUMMARY_MARKER = "pr-review-agent-v3-summary"
LEGACY_FINAL_COMMENT_MARKER = "pr-review-agent-v3-final-comment"
LEGACY_INLINE_MARKER_PREFIX = "pr-review-agent-v3-inline"


SEVERITY_RANK = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def sanitize_pr_comment_text(
    text: str,
) -> str:
    sanitized = re.sub(
        r"\bV3\b\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"\bAI\b",
        "automated",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"\bagents\b",
        "tools",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"\bagent\b",
        "tool",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"[ \t]{2,}",
        " ",
        sanitized,
    )
    return sanitized.strip()


def validate_configuration() -> None:
    missing = []

    if not os.getenv("GITHUB_TOKEN"):
        missing.append("GITHUB_TOKEN")

    if not os.getenv("GEMINI_API_KEY"):
        missing.append("GEMINI_API_KEY")

    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            "Missing configuration. Set these environment values "
            f"before running V3: {joined}"
        )


def resolve_repository_config() -> tuple[str, str]:
    owner = GITHUB_OWNER
    repo = GITHUB_REPO

    if not owner or not repo:
        inferred = infer_github_repo_from_remote()

        if inferred:
            inferred_owner, inferred_repo = inferred
            owner = owner or inferred_owner
            repo = repo or inferred_repo

    if owner and repo:
        return owner, repo

    print_header("SELECT REPOSITORY")
    print(
        "Repository was not configured and could not be inferred "
        "from the local Git remote."
    )

    if not owner:
        owner = input("GitHub owner or organization: ").strip()

    if not repo:
        repo = input("GitHub repository name: ").strip()

    if not owner or not repo:
        raise RuntimeError(
            "GitHub owner and repository are required to list open PRs."
        )

    return owner, repo


def infer_github_repo_from_remote(
    remote_name: str = "origin",
) -> tuple[str, str] | None:
    result = subprocess.run(
        [
            "git",
            "config",
            "--get",
            f"remote.{remote_name}.url",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None

    remote_url = result.stdout.strip()

    if not remote_url:
        return None

    match = re.search(
        r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
        remote_url,
    )

    if not match:
        return None

    return (
        match.group("owner"),
        match.group("repo"),
    )


def get_model_json(
    model: Any,
    indent: int = 2,
) -> str:
    if hasattr(model, "model_dump_json"):
        return model.model_dump_json(indent=indent)

    return model.json(indent=indent)


def get_model_dict(
    model: Any,
) -> dict:
    if hasattr(model, "model_dump"):
        try:
            return model.model_dump(mode="json")
        except TypeError:
            return model.model_dump()

    return model.dict()


def parse_review_result(
    response: Any,
) -> PRReviewResult:
    parsed = getattr(response, "parsed", None)

    if isinstance(parsed, PRReviewResult):
        return parsed

    if isinstance(parsed, dict):
        return PRReviewResult(**parsed)

    text = getattr(response, "text", "") or ""
    text = strip_json_fence(text)

    if hasattr(PRReviewResult, "model_validate_json"):
        return PRReviewResult.model_validate_json(text)

    return PRReviewResult.parse_raw(text)


def strip_json_fence(
    text: str,
) -> str:
    stripped = text.strip()

    if stripped.startswith("```"):
        stripped = re.sub(
            r"^```(?:json)?",
            "",
            stripped,
            flags=re.IGNORECASE,
        ).strip()
        stripped = re.sub(
            r"```$",
            "",
            stripped,
        ).strip()

    return stripped


def print_header(
    title: str,
) -> None:
    print()
    print("=" * 72)
    print(title.center(72))
    print("=" * 72)


def choose_pull_request(
    github: GitHubPRClientV3,
) -> dict | None:
    if PULL_REQUEST_NUMBER is not None:
        github.set_pull_request_number(PULL_REQUEST_NUMBER)
        pr = github.get_pull_request()

        if "error" in pr:
            raise RuntimeError(pr["error"])

        return pr

    print_header("SELECT PULL REQUEST")

    pr_list_state = normalized_pull_request_list_state()
    open_pull_requests = list_pull_requests_for_selection(
        github,
        pr_list_state,
    )

    if open_pull_requests and "error" in open_pull_requests[0]:
        raise RuntimeError(open_pull_requests[0]["error"])

    if not open_pull_requests:
        selected_number = read_manual_pull_request_number(
            pr_list_state
        )

        if selected_number is None:
            return None

        github.set_pull_request_number(selected_number)
        pr = github.get_pull_request()

        if "error" in pr:
            raise RuntimeError(pr["error"])

        return pr

    print(
        f"{pr_list_state.title()} pull requests for "
        f"{GITHUB_OWNER}/{GITHUB_REPO}:"
    )
    print()

    for index, pr in enumerate(
        open_pull_requests,
        start=1,
    ):
        draft_label = " [draft]" if pr.get("draft") else ""
        print(
            f"{index}. PR #{pr['number']}{draft_label}: "
            f"{pr['title']}"
        )
        print(
            f"   {pr['source_branch']} -> "
            f"{pr['target_branch']} | "
            f"author: {pr['author']} | "
            f"updated: {pr['updated_at']}"
        )

    print()
    print(
        "Choose the PR to review. Enter the list number "
        "or the GitHub PR number."
    )

    selected_number = read_pull_request_choice(
        open_pull_requests
    )
    github.set_pull_request_number(selected_number)

    pr = github.get_pull_request()

    if "error" in pr:
        raise RuntimeError(pr["error"])

    return pr


def list_pull_requests_for_selection(
    github: GitHubPRClientV3,
    pr_list_state: str,
) -> list[dict]:
    try:
        return github.list_open_pull_requests(
            state=pr_list_state,
        )
    except TypeError as error:
        if "unexpected keyword argument 'state'" not in str(error):
            raise

        print(
            "Your local github_pr.py does not support "
            "PR_LIST_STATE yet. Falling back to open PRs only."
        )
        return github.list_open_pull_requests()


def normalized_pull_request_list_state() -> str:
    if PR_LIST_STATE in {"open", "closed", "all"}:
        return PR_LIST_STATE

    print(
        f"Unsupported PR_LIST_STATE={PR_LIST_STATE!r}; "
        "using open."
    )
    return "open"


def read_manual_pull_request_number(
    pr_list_state: str,
) -> int | None:
    print(
        f"No {pr_list_state} pull requests found for "
        f"{GITHUB_OWNER}/{GITHUB_REPO}."
    )
    print()
    print(
        "If the PR is merged/closed, set PR_LIST_STATE=closed "
        "or PR_LIST_STATE=all in .env."
    )
    print(
        "You can also type a GitHub PR number now to review it "
        "directly. Press Enter to stop."
    )

    while True:
        answer = input("> ").strip()

        if not answer:
            print(
                "No pull request selected. Nothing to review."
            )
            return None

        if answer.isdigit() and int(answer) > 0:
            return int(answer)

        print("Please enter a valid PR number.")


def read_pull_request_choice(
    open_pull_requests: list[dict],
) -> int:
    by_list_index = {
        str(index): pr["number"]
        for index, pr in enumerate(
            open_pull_requests,
            start=1,
        )
    }
    by_pr_number = {
        str(pr["number"]): pr["number"]
        for pr in open_pull_requests
    }

    while True:
        answer = input("> ").strip()

        if answer in by_list_index:
            return by_list_index[answer]

        if answer in by_pr_number:
            return by_pr_number[answer]

        print(
            "Please enter one of the displayed list numbers "
            "or PR numbers."
        )


def run_static_analysis(
    github: GitHubPRClientV3,
    analyzer: CodeAnalyzer,
    changed_files: list[dict],
) -> list[ReviewFinding]:
    print_header("RUNNING STATIC ANALYSIS")

    findings: list[ReviewFinding] = []

    for file_data in changed_files:
        filepath = file_data.get("filename", "")

        if not filepath:
            continue

        if file_data.get("status") == "removed":
            continue

        print(f"Static analysis: {filepath}")

        content = github.read_repository_file(filepath)

        if not content or content.startswith("Unable to "):
            print(f"  Skipped: unable to read {filepath}")
            continue

        diff_lines = github.get_valid_diff_lines(filepath)
        allowed_lines = diff_lines or None
        file_findings = analyzer.analyze(
            filepath,
            content,
            allowed_lines=allowed_lines,
        )

        print(f"  Findings: {len(file_findings)}")
        findings.extend(file_findings)

    print(f"Total static findings: {len(findings)}")
    return findings


def run_xcode_analysis(
    github: GitHubPRClientV3,
) -> XcodeAnalysisResult:
    print_header("RUNNING XCODE BUILD/TEST ANALYSIS")

    try:
        with github.checkout_pull_request_head() as checkout:
            print(f"Checked out: {checkout.source_sha}")
            print(f"Workspace: {checkout.path}")

            analyzer = XcodeBuildAnalyzer(checkout.path)
            result = analyzer.analyze()

            print(result.summary)

            for command in result.commands:
                status = "PASS" if command.success else "FAIL"
                print(
                    f"{status}: {command.command} "
                    f"({command.duration_seconds}s)"
                )

            print(
                "Build/test findings: "
                f"{len(result.findings)}"
            )

            return result

    except Exception as error:
        message = (
            "Temporary checkout or xcodebuild analysis failed before "
            f"build/test execution completed: {error}"
        )
        print(message)

        return XcodeAnalysisResult(
            xcodebuild_available=False,
            summary=message,
        )


def ask_for_xcodebuild_validation() -> bool:
    print_header("XCODEBUILD VALIDATION")
    print(
        "Some iOS projects require a valid provisioning profile before "
        "xcodebuild can build or test successfully."
    )
    print()
    print(
        "Type RUN or YES to clone the PR head and run xcodebuild "
        "build/test validation."
    )
    print(
        "Press Enter or type SKIP to skip xcodebuild and continue with "
        "static + Gemini review."
    )

    answer = input("> ").strip().upper()
    return answer in {"RUN", "YES", "Y"}


def skipped_xcode_analysis_result() -> XcodeAnalysisResult:
    return XcodeAnalysisResult(
        xcodebuild_available=shutil.which("xcodebuild") is not None,
        summary=(
            "xcodebuild validation was skipped by reviewer before "
            "checkout/build/test execution. Static analysis and Gemini "
            "semantic review continued."
        ),
    )


def run_gemini_semantic_review(
    github: GitHubPRClientV3,
    pr: dict,
    changed_files: list[dict],
    static_findings: list[ReviewFinding],
    xcode_result: XcodeAnalysisResult,
) -> PRReviewResult:
    print_header("RUNNING GEMINI SEMANTIC REVIEW")

    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"]
    )

    def get_pull_request() -> dict:
        return pr

    def get_pull_request_files() -> list[dict]:
        return changed_files

    def get_file_diff(
        filepath: str,
    ) -> str:
        return github.get_file_diff(filepath)

    def read_repository_file(
        filepath: str,
    ) -> str:
        return github.read_repository_file(filepath)

    static_context = [
        get_model_dict(finding)
        for finding in static_findings[:20]
    ]
    xcode_context = get_model_dict(xcode_result)

    prompt = f"""
You are a senior software engineer performing a semantic GitHub pull
request review.

Repository:
{GITHUB_OWNER}/{GITHUB_REPO}

Pull Request:
#{pr["number"]}

IMPORTANT:
A deterministic static analyzer and an xcodebuild analyzer have already
run separately. Use those results as context, but do not simply repeat
the same mechanical findings unless you add meaningful semantic context.

Static findings already detected:
{json.dumps(static_context, indent=2)}

xcodebuild analysis:
{json.dumps(xcode_context, indent=2)}

AVAILABLE TOOLS:
1. get_pull_request()
2. get_pull_request_files()
3. get_file_diff(filepath)
4. read_repository_file(filepath)

MANDATORY WORKFLOW:
1. Call get_pull_request().
2. Call get_pull_request_files().
3. For every reviewable changed file, call get_file_diff(filepath).
4. Inspect every added or modified code block.
5. If the diff lacks enough context, call read_repository_file(filepath).
6. Review primarily issues introduced or exposed by this PR.

SEMANTIC FOCUS AREAS:
- correctness and business logic
- unhandled failure paths
- concurrency and thread safety
- Swift actor isolation and MainActor correctness
- memory ownership and closure capture lifetime
- state management and lifecycle
- API misuse
- security and sensitive data exposure
- performance regressions
- testability and missing tests

SWIFT / IOS CHECKLIST:
Evaluate newly added Swift code for try!, unsafe force unwraps, as!,
Task.detached, Task lifetime leaks, DispatchQueue usage, actor isolation,
@MainActor violations, Sendable issues, retain cycles, weak/unowned self,
SwiftUI state ownership, UIKit lifecycle issues, and cancellation.

INLINE COMMENT REQUIREMENT:
For every finding tied to a specific changed line, set line_number to the
new-file line number on the RIGHT side of the PR diff. Only provide a
line_number when you are confident that line exists inside the diff.
For file-wide or architecture-wide findings, set line_number to null.

REVIEW RULES:
- Never invent files, APIs, code, line numbers, or behavior.
- Only report findings supported by code you inspected.
- Do not report unrelated legacy issues unless this PR makes them newly
  relevant.
- Do not create findings just to make the report longer.
- If the PR is good, report no findings.

Return structured JSON matching the provided PRReviewResult schema.
Use source="GEMINI" for your semantic findings.
"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[
                get_pull_request,
                get_pull_request_files,
                get_file_diff,
                read_repository_file,
            ],
            response_mime_type="application/json",
            response_schema=PRReviewResult,
        ),
    )

    review = parse_review_result(response)
    print("Gemini semantic findings:", len(review.findings))
    return review


def merge_findings(
    findings: list[ReviewFinding],
) -> list[ReviewFinding]:
    merged: list[ReviewFinding] = []

    for finding in findings:
        duplicate_index = find_duplicate_index(
            merged,
            finding,
        )

        if duplicate_index is None:
            merged.append(finding)
            continue

        existing = merged[duplicate_index]

        if (
            SEVERITY_RANK[finding.severity]
            > SEVERITY_RANK[existing.severity]
        ):
            merged[duplicate_index] = finding

    return merged


def find_duplicate_index(
    existing_findings: list[ReviewFinding],
    candidate: ReviewFinding,
) -> int | None:
    for index, existing in enumerate(existing_findings):
        if not same_location(existing, candidate):
            continue

        if normalized(existing.title) == normalized(candidate.title):
            return index

        if (
            existing.category.lower()
            == candidate.category.lower()
        ):
            return index

        if word_overlap(existing.title, candidate.title) >= 0.5:
            return index

    return None


def same_location(
    left: ReviewFinding,
    right: ReviewFinding,
) -> bool:
    if left.file.lower() != right.file.lower():
        return False

    if left.line_number is None or right.line_number is None:
        return left.line_number == right.line_number

    return left.line_number == right.line_number


def normalized(
    value: str,
) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        value.lower(),
    ).strip()


def word_overlap(
    left: str,
    right: str,
) -> float:
    left_words = set(normalized(left).split())
    right_words = set(normalized(right).split())

    if not left_words or not right_words:
        return 0.0

    return len(left_words & right_words) / len(
        left_words | right_words
    )


def calculate_overall_risk(
    semantic_review: PRReviewResult,
    findings: list[ReviewFinding],
) -> Severity:
    severities = [
        semantic_review.overall_risk,
        *[
            finding.severity
            for finding in findings
        ],
    ]

    return max(
        severities,
        key=lambda severity: SEVERITY_RANK[severity],
    )


def calculate_recommendation(
    semantic_review: PRReviewResult,
    findings: list[ReviewFinding],
) -> Recommendation:
    if (
        semantic_review.final_recommendation
        == Recommendation.REQUEST_CHANGES
    ):
        return Recommendation.REQUEST_CHANGES

    if any(
        finding.severity
        in {Severity.CRITICAL, Severity.HIGH}
        for finding in findings
    ):
        return Recommendation.REQUEST_CHANGES

    if findings:
        return Recommendation.APPROVE_WITH_COMMENTS

    return Recommendation.APPROVE


def combine_review(
    semantic_review: PRReviewResult,
    static_findings: list[ReviewFinding],
    xcode_result: XcodeAnalysisResult,
) -> PRReviewResult:
    merged_findings = merge_findings(
        [
            *static_findings,
            *xcode_result.findings,
            *semantic_review.findings,
        ]
    )

    return PRReviewResult(
        pr_summary=semantic_review.pr_summary,
        overall_risk=calculate_overall_risk(
            semantic_review,
            merged_findings,
        ),
        files_reviewed=sorted(
            set(semantic_review.files_reviewed)
        ),
        findings=merged_findings,
        testing_recommendations=(
            semantic_review.testing_recommendations
        ),
        final_recommendation=calculate_recommendation(
            semantic_review,
            merged_findings,
        ),
    )


def print_review(
    review: PRReviewResult,
    xcode_result: XcodeAnalysisResult,
) -> None:
    print_header("V3 PR REVIEW RESULT")

    print(f"Summary: {review.pr_summary}")
    print(f"Overall risk: {review.overall_risk.value}")
    print(
        "Final recommendation: "
        f"{review.final_recommendation.value}"
    )
    print()
    print(f"xcodebuild: {xcode_result.summary}")
    print()

    if review.files_reviewed:
        print("Files reviewed:")

        for filepath in review.files_reviewed:
            print(f"- {filepath}")

        print()

    if not review.findings:
        print("Findings: none")
    else:
        print("Findings:")

        for index, finding in enumerate(
            review.findings,
            start=1,
        ):
            location = finding.file

            if finding.line_number is not None:
                location += f":{finding.line_number}"

            print(
                f"{index}. [{finding.severity.value}] "
                f"[{finding.source.value}] {location} - "
                f"{finding.title}"
            )
            print(f"   {finding.explanation}")

    if review.testing_recommendations:
        print()
        print("Testing recommendations:")

        for recommendation in review.testing_recommendations:
            print(f"- {recommendation}")

    print()
    print("Structured JSON:")
    print(get_model_json(review))


def save_review_artifacts(
    pr: dict,
    changed_files: list[dict],
    review: PRReviewResult,
    xcode_result: XcodeAnalysisResult,
    static_findings: list[ReviewFinding],
) -> tuple[Path, Path]:
    output_dir = resolve_review_output_dir()
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    generated_at = datetime.now(timezone.utc)
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    base_name = (
        f"pr-{pr['number']}-"
        f"{safe_filename(pr['title'])}-"
        f"{timestamp}"
    )
    json_path = output_dir / f"{base_name}.json"
    markdown_path = output_dir / f"{base_name}.md"

    payload = {
        "generated_at": generated_at.isoformat(),
        "repository": f"{GITHUB_OWNER}/{GITHUB_REPO}",
        "pull_request": {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "author": pr.get("author"),
            "source_branch": pr.get("source_branch"),
            "source_sha": pr.get("source_sha"),
            "target_branch": pr.get("target_branch"),
            "html_url": pr.get("html_url"),
        },
        "changed_files": changed_files,
        "static_findings": [
            get_model_dict(finding)
            for finding in static_findings
        ],
        "xcode_analysis": get_model_dict(xcode_result),
        "final_review": get_model_dict(review),
    }

    json_path.write_text(
        json.dumps(
            payload,
            indent=2,
        ),
        encoding="utf-8",
    )
    markdown_path.write_text(
        format_local_markdown_report(
            pr=pr,
            review=review,
            xcode_result=xcode_result,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )

    print()
    print("Saved local review artifacts:")
    print(f"- JSON: {json_path}")
    print(f"- Markdown: {markdown_path}")

    return json_path, markdown_path


def resolve_review_output_dir() -> Path:
    output_dir = REVIEW_OUTPUT_DIR or "review_outputs"
    path = Path(output_dir)

    if path.is_absolute():
        return path

    return Path(__file__).resolve().parent / path


def safe_filename(
    value: str,
    max_length: int = 80,
) -> str:
    normalized_value = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "-",
        value.strip().lower(),
    ).strip("-")

    if not normalized_value:
        normalized_value = "review"

    return normalized_value[:max_length]


def format_local_markdown_report(
    pr: dict,
    review: PRReviewResult,
    xcode_result: XcodeAnalysisResult,
    generated_at: datetime,
) -> str:
    lines = [
        f"# PR #{pr['number']} Review",
        "",
        f"Generated: {generated_at.isoformat()}",
        f"Repository: `{GITHUB_OWNER}/{GITHUB_REPO}`",
        f"PR: [{pr['title']}]({pr.get('html_url', '')})",
        f"Author: `{pr.get('author', '')}`",
        (
            f"Branches: `{pr.get('source_branch', '')}` -> "
            f"`{pr.get('target_branch', '')}`"
        ),
        f"Head SHA: `{pr.get('source_sha', '')}`",
        "",
        "## Result",
        "",
        f"Overall risk: **{review.overall_risk.value}**",
        (
            "Final recommendation: "
            f"**{review.final_recommendation.value}**"
        ),
        "",
        "## Summary",
        "",
        review.pr_summary,
        "",
        "## xcodebuild",
        "",
        xcode_result.summary or "No xcodebuild summary was produced.",
    ]

    if xcode_result.commands:
        lines.extend(
            [
                "",
                "### Commands",
                "",
            ]
        )

        for command in xcode_result.commands:
            status = "PASS" if command.success else "FAIL"
            lines.append(
                f"- **{status}** `{command.command}` "
                f"({command.duration_seconds}s)"
            )

    lines.extend(
        [
            "",
            "## Findings",
            "",
        ]
    )

    if not review.findings:
        lines.append("No findings were reported.")
    else:
        for index, finding in enumerate(
            review.findings,
            start=1,
        ):
            location = finding.file

            if finding.line_number is not None:
                location += f":{finding.line_number}"

            lines.extend(
                [
                    (
                        f"### {index}. [{finding.severity.value}] "
                        f"{finding.title}"
                    ),
                    "",
                    f"- Source: `{finding.source.value}`",
                    f"- Category: `{finding.category}`",
                    f"- Location: `{location}`",
                    f"- Explanation: {finding.explanation}",
                    f"- Why it matters: {finding.why_it_matters}",
                    f"- Suggested fix: {finding.suggested_fix}",
                    "",
                ]
            )

    if review.testing_recommendations:
        lines.extend(
            [
                "## Testing Recommendations",
                "",
            ]
        )

        for recommendation in review.testing_recommendations:
            lines.append(f"- {recommendation}")

    return "\n".join(lines).rstrip() + "\n"


def format_inline_comment(
    finding: ReviewFinding,
) -> str:
    marker = inline_comment_marker(finding)

    return sanitize_pr_comment_text(
        f"<!-- {marker} -->\n"
        f"**[{finding.severity.value}] "
        f"{finding.title}**\n\n"
        f"{finding.explanation}\n\n"
        f"**Why it matters:** {finding.why_it_matters}\n\n"
        f"**Suggested fix:** {finding.suggested_fix}\n\n"
        f"_Source: {finding.source.value.lower()} analysis_"
    )


def format_summary_comment(
    review: PRReviewResult,
    xcode_result: XcodeAnalysisResult,
) -> str:
    lines = [
        f"<!-- {SUMMARY_MARKER} -->",
        "## PR Review",
        "",
        f"**Overall risk:** {review.overall_risk.value}",
        (
            "**Final recommendation:** "
            f"{review.final_recommendation.value}"
        ),
        "",
        f"**Summary:** {review.pr_summary}",
        "",
        f"**xcodebuild:** {xcode_result.summary}",
        "",
        "### Findings",
    ]

    if not review.findings:
        lines.extend(
            [
                "No findings were reported.",
                "",
                (
                    "No inline comments were posted because this review "
                    "did not identify line-level issues."
                ),
            ]
        )
    else:
        for index, finding in enumerate(
            review.findings,
            start=1,
        ):
            location = finding.file

            if finding.line_number is not None:
                location += f":{finding.line_number}"

            lines.extend(
                [
                    "",
                    (
                        f"{index}. **[{finding.severity.value}] "
                        f"[{finding.source.value}] "
                        f"{finding.title}**"
                    ),
                    f"   - Location: `{location}`",
                    f"   - Explanation: {finding.explanation}",
                    f"   - Suggested fix: {finding.suggested_fix}",
                ]
            )

    if review.testing_recommendations:
        lines.extend(
            [
                "",
                "### Testing Recommendations",
            ]
        )

        for recommendation in review.testing_recommendations:
            lines.append(f"- {recommendation}")

    return sanitize_pr_comment_text("\n".join(lines))


def format_final_pr_comment(
    review: PRReviewResult,
    xcode_result: XcodeAnalysisResult,
    posted_inline: int,
    skipped_duplicates: int,
    summary_posted: bool,
) -> str:
    if review.final_recommendation == Recommendation.REQUEST_CHANGES:
        closing_note = (
            "Please address the blocking findings before this PR is merged."
        )
    elif review.final_recommendation == Recommendation.APPROVE_WITH_COMMENTS:
        closing_note = (
            "This PR can move forward after the reviewer considers the "
            "comments and testing recommendations above."
        )
    else:
        closing_note = (
            "Approved: no blocking findings were reported by the review."
        )

    summary_status = "posted or updated" if summary_posted else "not posted"

    lines = [
        f"<!-- {FINAL_COMMENT_MARKER} -->",
        "## Final Review Comment",
        "",
        f"**Final recommendation:** {review.final_recommendation.value}",
        f"**Overall risk:** {review.overall_risk.value}",
        f"**Detailed review summary:** {summary_status}",
        f"**Inline comments posted:** {posted_inline}",
        f"**Duplicate inline comments skipped:** {skipped_duplicates}",
        "",
        f"**Build/test status:** {xcode_result.summary}",
        "",
        closing_note,
        "",
        "_Generated after reviewer approval._",
    ]

    return sanitize_pr_comment_text("\n".join(lines))


def inline_comment_marker(
    finding: ReviewFinding,
) -> str:
    return (
        f"{INLINE_MARKER_PREFIX}:"
        f"{finding_fingerprint(finding)}"
    )


def legacy_inline_comment_marker(
    finding: ReviewFinding,
) -> str:
    return (
        f"{LEGACY_INLINE_MARKER_PREFIX}:"
        f"{finding_fingerprint(finding)}"
    )


def finding_fingerprint(
    finding: ReviewFinding,
) -> str:
    payload = "|".join(
        [
            finding.file.lower(),
            str(finding.line_number or ""),
            finding.source.value,
            finding.category.lower(),
            normalized(finding.title),
        ]
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:16]


def ask_for_publish_approval(
    review: PRReviewResult,
) -> bool:
    print()
    print(
        "Human approval required before posting to GitHub."
    )

    if review.final_recommendation == Recommendation.APPROVE:
        print(
            "Type POST, APPROVE, or APPROVED to publish the approved "
            "workflow comments. Anything else skips publishing."
        )
    else:
        print(
            "Type POST to publish inline, summary, and final comments. "
            "Anything else skips publishing."
        )

    answer = input("> ").strip().upper()

    if answer == "POST":
        return True

    if review.final_recommendation == Recommendation.APPROVE:
        return answer in {"APPROVE", "APPROVED"}

    return False


def ask_for_duplicate_skip_confirmation(
    duplicate_findings: list[ReviewFinding],
) -> bool:
    if not duplicate_findings:
        return False

    print()
    print(
        f"Found {len(duplicate_findings)} duplicate inline "
        "comment(s) that were already posted on this PR."
    )

    for index, finding in enumerate(
        duplicate_findings[:10],
        start=1,
    ):
        location = finding.file

        if finding.line_number is not None:
            location += f":{finding.line_number}"

        print(
            f"{index}. [{finding.severity.value}] "
            f"{location} - {finding.title}"
        )

    if len(duplicate_findings) > 10:
        print(
            f"...and {len(duplicate_findings) - 10} more."
        )

    print()
    print(
        "Type SKIP to skip these duplicate inline comments. "
        "Anything else posts them again."
    )

    answer = input("> ").strip()
    return answer == "SKIP"


def find_duplicate_inline_findings(
    review: PRReviewResult,
    existing_inline_markers: set[str],
) -> list[ReviewFinding]:
    duplicate_findings: list[ReviewFinding] = []

    for finding in review.findings:
        if finding.line_number is None:
            continue

        markers = {
            inline_comment_marker(finding),
            legacy_inline_comment_marker(finding),
        }

        if markers & existing_inline_markers:
            duplicate_findings.append(finding)

    return duplicate_findings


def publish_review(
    github: GitHubPRClientV3,
    review: PRReviewResult,
    xcode_result: XcodeAnalysisResult,
) -> None:
    print_header("PUBLISHING REVIEW TO GITHUB")

    posted_inline = 0
    skipped_duplicates = 0
    existing_inline_markers = (
        github.get_existing_inline_comment_markers(
            [
                INLINE_MARKER_PREFIX,
                LEGACY_INLINE_MARKER_PREFIX,
            ]
        )
    )
    duplicate_findings = find_duplicate_inline_findings(
        review,
        existing_inline_markers,
    )
    skip_duplicates = ask_for_duplicate_skip_confirmation(
        duplicate_findings
    )

    for finding in review.findings:
        if posted_inline >= MAX_INLINE_COMMENTS:
            break

        if finding.line_number is None:
            continue

        marker = inline_comment_marker(finding)
        legacy_marker = legacy_inline_comment_marker(finding)

        if (
            skip_duplicates
            and (
                marker in existing_inline_markers
                or legacy_marker in existing_inline_markers
            )
        ):
            skipped_duplicates += 1
            continue

        if github.post_inline_review_comment(
            filepath=finding.file,
            line_number=finding.line_number,
            comment_body=format_inline_comment(finding),
        ):
            posted_inline += 1
            existing_inline_markers.add(marker)

    if (
        posted_inline == 0
        and review.final_recommendation == Recommendation.APPROVE
    ):
        print(
            "Approved workflow has no line-level findings. "
            "Posting PR-level approval comments instead."
        )

    summary_posted = github.upsert_pull_request_comment(
        [
            SUMMARY_MARKER,
            LEGACY_SUMMARY_MARKER,
        ],
        format_summary_comment(
            review,
            xcode_result,
        ),
        comment_label="Review summary comment",
    )
    final_comment_posted = github.upsert_pull_request_comment(
        [
            FINAL_COMMENT_MARKER,
            LEGACY_FINAL_COMMENT_MARKER,
        ],
        format_final_pr_comment(
            review=review,
            xcode_result=xcode_result,
            posted_inline=posted_inline,
            skipped_duplicates=skipped_duplicates,
            summary_posted=summary_posted,
        ),
        comment_label="Final review comment",
    )

    print(
        f"Posted inline comments: {posted_inline}; "
        f"skipped duplicates: {skipped_duplicates}; "
        f"summary posted: {summary_posted}; "
        f"final comment posted: {final_comment_posted}"
    )

    if not summary_posted and not final_comment_posted:
        print(
            "No PR-level comments were posted. Check that the GitHub "
            "token has Pull requests: write and Issues: write permission."
        )


def main() -> None:
    global GITHUB_OWNER
    global GITHUB_REPO

    validate_configuration()

    print_header("STARTING V3 PR REVIEW")
    print(f"V3 script version: {V3_SCRIPT_VERSION}")

    GITHUB_OWNER, GITHUB_REPO = resolve_repository_config()

    github = GitHubPRClientV3(
        owner=GITHUB_OWNER,
        repo=GITHUB_REPO,
    )
    static_analyzer = CodeAnalyzer()

    pr = choose_pull_request(github)

    if pr is None:
        print()
        print("PR review stopped without error.")
        return

    print(
        f"Reviewing {GITHUB_OWNER}/{GITHUB_REPO} "
        f"PR #{pr['number']}"
    )
    print(f"PR title: {pr['title']}")
    print(f"PR head SHA: {pr['source_sha']}")

    changed_files = github.get_pull_request_files()

    if changed_files and "error" in changed_files[0]:
        raise RuntimeError(changed_files[0]["error"])

    static_findings = run_static_analysis(
        github,
        static_analyzer,
        changed_files,
    )
    if ask_for_xcodebuild_validation():
        xcode_result = run_xcode_analysis(github)
    else:
        xcode_result = skipped_xcode_analysis_result()
        print(xcode_result.summary)

    semantic_review = run_gemini_semantic_review(
        github=github,
        pr=pr,
        changed_files=changed_files,
        static_findings=static_findings,
        xcode_result=xcode_result,
    )
    final_review = combine_review(
        semantic_review=semantic_review,
        static_findings=static_findings,
        xcode_result=xcode_result,
    )

    print_review(
        final_review,
        xcode_result,
    )
    save_review_artifacts(
        pr=pr,
        changed_files=changed_files,
        review=final_review,
        xcode_result=xcode_result,
        static_findings=static_findings,
    )

    if ask_for_publish_approval(final_review):
        publish_review(
            github,
            final_review,
            xcode_result,
        )
    else:
        print("Publishing skipped.")


if __name__ == "__main__":
    main()
