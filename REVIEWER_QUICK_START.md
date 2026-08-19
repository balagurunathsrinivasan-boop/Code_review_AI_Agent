# Reviewer Quick Start

This folder is a local copy of the PR review tool.

Use it when a reviewer wants to choose a GitHub repository, select an available pull request, run static analysis, run Xcode build/test checks when possible, run Gemini review, and post comments only after human approval.

## One-Click Start

Double-click:

```text
START_PR_REVIEW_AGENT.command
```

The launcher will:

- create `.env` if needed
- open `.env` once at the start of every run
- ask the reviewer to review or update the GitHub details before continuing
- stop with a missing-value list instead of reopening `.env` repeatedly
- create `.venv` if needed
- install Python packages
- start the PR review

If macOS asks for permission to run the file, allow it.

## Manual Start

Use these steps only if you prefer Terminal.

## 1. Open This Folder

```bash
cd "/Users/mynest/Documents/Codex/2026-08-17/referenced-chatgpt-conversation-this-is-an-2/outputs/pr_review_agent_reviewer_local"
```

## 2. Run First-Time Setup

```bash
bash setup_local.sh
```

This creates `.venv` and installs the required Python packages.

## 3. Open `.env`

```bash
open -a TextEdit .env
```

## 4. Configure GitHub And Gemini

Fill these values:

```text
GITHUB_TOKEN=your_github_token
GEMINI_API_KEY=your_gemini_api_key
GITHUB_OWNER=github_owner_or_org
GITHUB_REPO=repository_name
```

For this GitHub URL:

```text
https://github.com/acme/mobile-app
```

Use:

```text
GITHUB_OWNER=acme
GITHUB_REPO=mobile-app
```

## 5. Choose Which PRs To Show

Usually keep:

```text
PR_LIST_STATE=open
```

Use this if the reviewer wants to see closed and merged PRs too:

```text
PR_LIST_STATE=all
```

Use this to review one known PR directly:

```text
PULL_REQUEST_NUMBER=123
```

Leave `PULL_REQUEST_NUMBER` blank if the reviewer should choose from the list.

## 6. Run The Review

```bash
bash run_review.sh
```

The agent will:

- list available PRs
- let the reviewer choose the right PR
- inspect changed files
- ask whether xcodebuild validation should run
- if approved, clone the PR head SHA into a temporary workspace
- if approved, run Xcode build/test analysis when possible
- run static analysis
- run Gemini semantic review
- save local reports under `review_outputs`
- ask for approval before posting GitHub comments
- post or update a final closing PR comment after approval

## 7. Posting Comments

The agent will not post comments automatically.

At the approval prompt:

```text
POST
```

posts inline comments, a detailed summary comment, and a final closing comment to GitHub.

When the final recommendation is `APPROVE`, the reviewer can also type:

```text
APPROVE
```

The approved workflow still posts PR-level comments even when there are no inline findings.

Anything else cancels GitHub posting and keeps the local review reports only.

If duplicate comments already exist, the agent asks before skipping them. Type:

```text
SKIP
```

to skip duplicate inline comments.

## GitHub Token Permissions

For a fine-grained GitHub token, use:

```text
Repository contents: read
Pull requests: read/write
Issues: read/write
Metadata: read
```

Do not commit `.env` or share real tokens.
