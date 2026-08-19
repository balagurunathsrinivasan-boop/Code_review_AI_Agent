# PR Review Documentation

Version: `2026-08-19.1`

This document explains how to set up, configure, run, troubleshoot, and operate the PR review tool.

## One-Click Local Start

For the standalone reviewer copy, double-click:

```text
START_PR_REVIEW_AGENT.command
```

The launcher opens `.env` once on every run, waits for the reviewer to confirm the GitHub details, creates the local Python environment, installs dependencies, and starts the review agent.

If required values are still blank or placeholders after the reviewer presses Enter, the launcher lists the missing values and stops instead of reopening `.env` repeatedly.

## What It Does

This is a build-aware GitHub pull request review tool. It combines deterministic checks with AI semantic review, then asks for human approval before posting anything to GitHub.

The flow is:

```text
GitHub repository
    -> list PRs
    -> reviewer chooses PR
    -> fetch PR metadata and changed files
    -> run deterministic static analysis
    -> ask reviewer whether xcodebuild validation should run
    -> if approved, clone PR head SHA into a temporary folder
    -> if approved, discover Xcode projects/workspaces and schemes
    -> if approved, run xcodebuild build/test when feasible
    -> run Gemini semantic review
    -> merge and deduplicate findings
    -> save local JSON/Markdown artifacts
    -> ask human before GitHub posting
    -> post inline, summary, and final comments only after approval
```

## Files

The package contains:

```text
main.py
github_pr.py
review_models.py
code_analyzer.py
build_analyzer.py
.env.example
.gitignore
README.md
```

### `main.py`

Main orchestrator.

Responsibilities:

- Loads `.env`
- Resolves GitHub repository owner/name
- Lists PRs and lets the reviewer choose one
- Runs static analysis
- Runs Xcode build/test analysis
- Runs Gemini semantic review
- Merges/deduplicates findings
- Saves local review artifacts
- Asks for human approval before posting to GitHub
- Handles duplicate inline comments

### `github_pr.py`

GitHub API client.

Responsibilities:

- Fetch PR metadata
- List PRs
- Fetch changed PR files
- Fetch file diffs
- Read files from PR head SHA
- Validate inline-comment line numbers against the GitHub diff
- Clone and checkout PR head SHA
- Post inline PR comments
- Post or update detailed summary comments
- Post or update final closing PR comments
- Detect existing inline comment markers

### `review_models.py`

Shared Pydantic models.

Includes:

- `Severity`
- `Recommendation`
- `FindingSource`
- `ReviewFinding`
- `PRReviewResult`
- `BuildRunSummary`
- `XcodeTarget`
- `XcodeAnalysisResult`

### `code_analyzer.py`

Deterministic static analyzer.

It checks changed lines for patterns such as:

- Swift `try!`
- Swift `as!`
- Swift force unwraps
- Swift `fatalError`
- Swift `preconditionFailure`
- Kotlin `!!`
- JavaScript/TypeScript `eval`
- React `dangerouslySetInnerHTML`
- Python `eval`
- Python `subprocess(..., shell=True)`
- Possible hard-coded secrets

### `build_analyzer.py`

Xcode build/test analyzer.

Responsibilities:

- Detect `.xcworkspace` and `.xcodeproj`
- Discover schemes
- Infer destinations when possible
- Run `xcodebuild build`
- Run `xcodebuild test` when feasible
- Convert compiler/test output into structured findings

## Requirements

Install Python packages used by the review tool:

```bash
pip install requests pydantic google-genai
```

You also need:

```text
Python 3.10+
git
GitHub token
Gemini API key
```

For Xcode build/test analysis:

```text
macOS
Xcode
xcodebuild
```

Before running `xcodebuild`, the tool asks the reviewer whether build/test validation should run. This is useful when the reviewer does not yet have a valid provisioning profile. If the reviewer skips it, continues with static + Gemini review.

If `xcodebuild` is unavailable, the tool also skips build/test analysis and continues with static + Gemini review.

## GitHub Token

reads the token from:

```text
GITHUB_TOKEN
```

The token is used for:

- Listing PRs
- Reading PR metadata
- Reading changed files
- Cloning private repositories over HTTPS
- Posting comments after approval

Recommended permissions for a fine-grained GitHub token:

```text
Repository contents: read
Pull requests: read/write
Issues: read/write
Metadata: read
```

Why Issues permission matters: GitHub PR summary comments are created through the issue comments API because every pull request is also an issue thread.

Do not paste real tokens into chat, source code, screenshots, commits, or Markdown reports.

## Gemini API Key

reads the Gemini key from:

```text
GEMINI_API_KEY
```

Default model:

```text
gemini-3.5-flash-lite
```

Override it in `.env`:

```text
GEMINI_MODEL=your-model-name
```

## `.env` Setup

has a built-in `.env` loader. You do not need `python-dotenv`.

Create `.env` in the same folder as `main.py`.

If `.env.example` exists:

```bash
cp .env.example .env
```

If `.env.example` does not exist:

```bash
touch .env
open -a TextEdit .env
```

Minimum `.env`:

```text
GITHUB_TOKEN=your_github_token_here
GEMINI_API_KEY=your_gemini_api_key_here
```

Recommended `.env`:

```text
GITHUB_TOKEN=your_github_token_here
GEMINI_API_KEY=your_gemini_api_key_here

GITHUB_OWNER=balagurunathpersonal-alt
GITHUB_REPO=AI_Agent_Simple_PR
PULL_REQUEST_NUMBER=
PR_LIST_STATE=open

GEMINI_MODEL=gemini-3.5-flash-lite
MAX_INLINE_COMMENTS=20
GITHUB_API_VERSION=
REVIEW_OUTPUT_DIR=review_outputs

XCODEBUILD_TIMEOUT_SECONDS=1800
XCODEBUILD_MAX_SCHEMES=3
XCODEBUILD_DESTINATION=
XCODEBUILD_SCHEME=
XCODEBUILD_CONTAINER=
XCODEBUILD_SKIP_TESTS=0
XCODEBUILD_ALLOW_TEST_WITHOUT_DESTINATION=0
XCODEBUILD_EXTRA_ARGS=
```

## Configuration Reference

### Required

```text
GITHUB_TOKEN
GEMINI_API_KEY
```

### Repository Selection

```text
GITHUB_OWNER
GITHUB_REPO
```

If these are blank, the tool tries to infer the repo from:

```bash
git config --get remote.origin.url
```

For example, this remote:

```text
https://github.com/balagurunathpersonal-alt/AI_Agent_Simple_PR.git
```

is inferred as:

```text
GITHUB_OWNER=balagurunathpersonal-alt
GITHUB_REPO=AI_Agent_Simple_PR
```

### PR Selection

```text
PULL_REQUEST_NUMBER=
```

Leave blank to show a PR picker.

Set a number to skip the picker:

```text
PULL_REQUEST_NUMBER=12
```

### PR List State

```text
PR_LIST_STATE=open
```

Allowed values:

```text
open
closed
all
```

Use `closed` or `all` if the PR was already merged or closed.

### Gemini Model

```text
GEMINI_MODEL=gemini-3.5-flash-lite
```

Use a stronger model for more complex code review if your account/project supports it.

### GitHub API Version

```text
GITHUB_API_VERSION=
```

Leave blank to omit the GitHub API version header.

Set it only if you intentionally want a specific GitHub API version:

```text
GITHUB_API_VERSION=2022-11-28
```

### Max Inline Comments

```text
MAX_INLINE_COMMENTS=20
```

Limits how many inline comments the tool posts in one run.

### Local Report Output Folder

```text
REVIEW_OUTPUT_DIR=review_outputs
```

After every completed review, the tool writes:

```text
review_outputs/pr-<number>-<title>-<timestamp>.json
review_outputs/pr-<number>-<title>-<timestamp>.md
```

### Xcode Settings

asks before running xcodebuild. The settings below are used only when the reviewer chooses to run xcodebuild validation.

```text
XCODEBUILD_TIMEOUT_SECONDS=1800
XCODEBUILD_MAX_SCHEMES=3
XCODEBUILD_DESTINATION=
XCODEBUILD_SCHEME=
XCODEBUILD_CONTAINER=
XCODEBUILD_SKIP_TESTS=0
XCODEBUILD_ALLOW_TEST_WITHOUT_DESTINATION=0
XCODEBUILD_EXTRA_ARGS=
```

Common examples:

Run only one scheme:

```text
XCODEBUILD_SCHEME=MyApp
```

Run a specific workspace/project:

```text
XCODEBUILD_CONTAINER=MyApp.xcworkspace
```

Set an explicit destination:

```text
XCODEBUILD_DESTINATION=platform=iOS Simulator,name=iPhone 15
```

Skip tests:

```text
XCODEBUILD_SKIP_TESTS=1
```

Add extra `xcodebuild` arguments:

```text
XCODEBUILD_EXTRA_ARGS=-configuration Debug
```

## Running The ReviewFrom the folder containing the files:

```bash
python main.py
```

Expected startup:

```text
========================================================================
                         STARTING PR REVIEW
========================================================================
script version: 2026-08-19.1
```

If you do not see the version line, your local `main.py` is not the latest copy.

## PR Picker

If `PULL_REQUEST_NUMBER` is blank, the tool lists PRs:

```text
Open pull requests for owner/repo:

1. PR #12: Fix login crash
   feature/login-fix -> main | author: user | updated: ...

Choose the PR to review. Enter the list number or the GitHub PR number.
```

You can enter:

```text
1
```

or:

```text
12
```

## No PR Available

If no PR is found, the tool stops gracefully:

```text
No open pull requests found for owner/repo.

You can also type a GitHub PR number now to review it directly. Press Enter to stop.
>

No pull request selected. Nothing to review.

PR review stopped without error.
```

No traceback should appear for this case.

If your PR is closed or merged, set:

```text
PR_LIST_STATE=all
```

or:

```text
PR_LIST_STATE=closed
```

## What Happens During Review

### 1. Static Analysis

reads changed files from the PR head SHA and runs deterministic checks only against diff-valid lines where possible.

Example output:

```text
RUNNING STATIC ANALYSIS
Static analysis: Sources/ProfileViewModel.swift
  Findings: 1
```

### 2. Xcode Build/Test Analysis

clones the PR head SHA into a temporary folder.

Then it:

- Finds Xcode workspaces/projects
- Discovers schemes
- Runs build
- Runs tests when feasible
- Extracts compiler/test failures into structured findings

If there is no Xcode project:

```text
No .xcworkspace or .xcodeproj containers were found, so xcodebuild analysis was skipped.
```

### 3. Gemini Semantic Review

Gemini inspects:

- PR metadata
- Changed files
- File diffs
- Full file content when needed
- Static findings
- Build/test findings

Gemini focuses on reasoning-heavy issues:

- correctness
- business logic
- concurrency
- Swift actor isolation
- `@MainActor`
- memory ownership
- state management
- lifecycle
- API misuse
- security
- performance
- missing tests

### 4. Merge and Deduplicate

Findings from static analysis, Xcode, and Gemini are merged.

deduplicates findings that point to the same file/line and similar title/category.

### 5. Save Local Artifacts

Before GitHub posting, the tool saves:

```text
JSON report
Markdown report
```

These are useful for:

- audit history
- offline review
- debugging
- comparing multiple runs
- sharing results without posting comments

### 6. Human Approval

asks:

```text
Human approval required before posting to GitHub.
Type POST to publish inline, summary, and final comments. Anything else skips publishing.
```

This input publishes comments for any final recommendation:

```text
POST
```

When the final recommendation is `APPROVE`, the tool also accepts:

```text
APPROVE
APPROVED
```

Approved reviews usually have no line-level findings, so PR-level approval comments are posted even when there are no inline comments to add.

Any other input skips GitHub publishing.

After approval, the tool posts or updates:

- inline comments for selected findings
- one detailed summary comment
- one final closing PR comment

The final closing comment uses its own hidden marker, so rerunning the tool updates the same final comment instead of adding repeated closing comments.

## GitHub Publishing Behavior

After approval, the tool posts:

- Inline comments for findings with valid diff line numbers
- One summary comment on the PR thread

Summary comments are upserted:

- First run creates the summary
- Later runs update the existing summary

Inline comments include hidden markers:

```text
<!-- pr-review-inline:<fingerprint> -->
```

The summary includes:

```text
<!-- pr-review-summary -->
```

These markers help detect duplicate comments.

## Duplicate Inline Comments

If duplicate inline comments are found, itThe tool asks:

```text
Found 2 duplicate inline comment(s) that were already posted on this PR.

Type SKIP to skip these duplicate inline comments. Anything else posts them again.
```

Type:

```text
SKIP
```

to avoid duplicate inline comments.

## Local Artifacts

Default folder:

```text
review_outputs/
```

Generated JSON contains:

- generation time
- repository
- PR metadata
- changed files
- static findings
- Xcode analysis result
- final merged review

Generated Markdown contains:

- PR summary
- risk
- recommendation
- xcodebuild result
- findings
- testing recommendations

## Keeping Files in Sync

When updating your working folder from the packaged output, update related files together.

At minimum, keep these two in sync:

```text
main.py
github_pr.py
```

If they get out of sync, you may see errors like:

```text
TypeError: GitHubPRClient.list_open_pull_requests() got an unexpected keyword argument 'state'
```

That means:

```text
main.py is newer
github_pr.py is older
```

From inside your working folder, update both:

```bash
cp "/Users/mynest/Documents/Codex/2026-08-17/referenced-chatgpt-conversation-this-is-an-2/outputs/pr_review_agent/main.py" .
cp "/Users/mynest/Documents/Codex/2026-08-17/referenced-chatgpt-conversation-this-is-an-2/outputs/pr_review_agent/github_pr.py" .
```

The final `.` means "copy into the current folder".

Verify:

```bash
grep -n "SCRIPT_VERSION" main.py
```

Expected:

```text
SCRIPT_VERSION = "2026-08-19.1"
```

## `.gitignore`

Recommended `.gitignore`:

```text
.env
__pycache__/
*.pyc
review_outputs/
```

Do not commit:

```text
.env
review_outputs/
__pycache__/
```

It is safe to commit:

```text
.env.example
README.md
```

## Troubleshooting

### `Missing configuration: GITHUB_TOKEN`

Cause:

```text
.env does not exist, is in the wrong folder, or does not contain GITHUB_TOKEN.
```

Fix:

```bash
open -a TextEdit .env
```

Make sure it contains:

```text
GITHUB_TOKEN=your_actual_token
```

### `Missing configuration: GEMINI_API_KEY`

Fix `.env`:

```text
GEMINI_API_KEY=your_actual_gemini_key
```

### `code .env` gives `command not found`

Use TextEdit:

```bash
open -a TextEdit .env
```

### `.env.example: No such file or directory`

Create `.env` directly:

```bash
touch .env
open -a TextEdit .env
```

Then paste your config.

### `cp: usage...`

`cp` needs both source and destination.

Correct:

```bash
cp "/path/to/main.py" .
```

Incorrect:

```bash
cp "/path/to/main.py"
```

### `unexpected keyword argument 'state'`

Cause:

```text
main.py and github_pr.py are out of sync.
```

Fix:

```bash
cp "/Users/mynest/Documents/Codex/2026-08-17/referenced-chatgpt-conversation-this-is-an-2/outputs/pr_review_agent/main.py" .
cp "/Users/mynest/Documents/Codex/2026-08-17/referenced-chatgpt-conversation-this-is-an-2/outputs/pr_review_agent/github_pr.py" .
```

### No open pull requests found

This is not an agent failure. It means GitHub returned no PRs for the selected state.

Options:

- Create/open a PR
- Set `PR_LIST_STATE=all`
- Set `PR_LIST_STATE=closed`
- Type a PR number manually when prompted
- Set `PULL_REQUEST_NUMBER=<number>` in `.env`

### GitHub returns `401`

Usually token issue.

Check:

```text
GITHUB_TOKEN is present
token is not expired
token has access to the repository
```

### GitHub returns `403`

Usually permission issue.

For posting comments, the token needs write access for pull requests/issues.

### GitHub returns `404`

Possible causes:

- Wrong owner/repo
- Token cannot access private repo
- PR number does not exist in that repo

### xcodebuild skipped

Possible causes:

- Reviewer chose to skip xcodebuild validation
- Running on a non-Mac machine
- Xcode is not installed
- No `.xcodeproj` or `.xcworkspace`
- Scheme discovery failed
- A provisioning profile is not available yet

The rest of the review can still complete.

### xcodebuild destination issues

Set destination explicitly:

```text
XCODEBUILD_DESTINATION=platform=iOS Simulator,name=iPhone 15
```

### Gemini structured output parsing fails

Possible causes:

- Model does not support the requested structured output behavior
- Model returned malformed JSON
- API error occurred

Try a stronger model or rerun.

## Safe Operating Checklist

Before running:

```text
.env exists
GITHUB_TOKEN is set
GEMINI_API_KEY is set
main.py and github_pr.py are in sync
target repo has the PR you want to review
```

Before typing `POST`:

```text
Read the local output
Check inline findings
Check line numbers
Check whether duplicates are being skipped
Confirm token is allowed to post comments
```

After running:

```text
Review JSON/Markdown artifacts
Check GitHub summary comment
Check inline comments
Keep .env private
```

## Common Commands

Run:

```bash
python main.py
```

Open `.env`:

```bash
open -a TextEdit .env
```

Check script version:

```bash
grep -n "SCRIPT_VERSION" main.py
```

Check Git remote:

```bash
git config --get remote.origin.url
```

Check generated reports:

```bash
ls -la review_outputs
```

## Current Limitations

- Xcode destination inference is best-effort.
- Build/test analysis currently focuses on Xcode projects.
- Static analysis is pattern-based and intentionally conservative.
- Gemini semantic review quality depends on model capability and PR size.
- Inline comments can only be posted on valid RIGHT-side diff lines.
- Very large PRs may require model/context tuning.

## Recommended Next Improvements

Possible V4 directions:

- Add GitHub Actions CI status ingestion
- Add support for non-Xcode build systems
- Add language-specific analyzers for Python, JavaScript, Kotlin, and backend repos
- Add a dry-run publish preview
- Add an HTML report
- Add SARIF output
- Add config validation at startup
- Add unit tests for diff-line parsing and duplicate markers
- Add release packaging so file sync is simpler
