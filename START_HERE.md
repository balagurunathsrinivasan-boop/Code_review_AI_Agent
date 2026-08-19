# Start Here

Double-click this file:

```text
START_PR_REVIEW_AGENT.command
```

The launcher opens `.env` once at the start of every run. Review or update the GitHub details, save the file, then return to the launcher window and press Enter.

If required values are still blank or placeholders, the launcher lists what is missing and stops. It will not keep reopening `.env`; save the file and double-click the launcher again.

After that, it prepares the local environment and starts the PR review agent. Before any xcodebuild validation, the agent asks whether the reviewer wants to run it. Press Enter or type `SKIP` to continue without xcodebuild when provisioning profiles are not ready.

Required `.env` values:

```text
GITHUB_TOKEN=your_github_token
GEMINI_API_KEY=your_gemini_api_key
GITHUB_OWNER=github_owner_or_org
GITHUB_REPO=repository_name
```

When the review finishes, the agent asks before posting anything to GitHub. After approval, it posts inline comments when there are findings, plus a detailed summary comment and a final closing comment. For approved reviews with no findings, it still posts the PR-level approval comments.
