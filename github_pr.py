from __future__ import annotations

import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import requests


GITHUB_API_URL = "https://api.github.com"


SUPPORTED_EXTENSIONS = {
    ".swift",
    ".m",
    ".mm",
    ".h",
    ".plist",
    ".xcconfig",
    ".kt",
    ".kts",
    ".java",
    ".xml",
    ".gradle",
    ".dart",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".py",
    ".go",
    ".rb",
    ".php",
    ".cs",
    ".cpp",
    ".c",
    ".html",
    ".css",
    ".scss",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
    ".md",
}


SUPPORTED_FILENAMES = {
    "Dockerfile",
    "Podfile",
    "Gemfile",
    "Fastfile",
    "Cartfile",
    "Makefile",
}


@dataclass(frozen=True)
class PullRequestCheckout:
    path: Path
    source_sha: str
    source_repo: str


class GitHubPRClientV3:
    def __init__(
        self,
        owner: str,
        repo: str,
        pull_request_number: int | None = None,
        api_version: str | None = None,
    ):
        self.owner = owner
        self.repo = repo
        self.pull_request_number = pull_request_number
        self.token = os.environ["GITHUB_TOKEN"]
        self.api_version = (
            api_version
            if api_version is not None
            else os.getenv("GITHUB_API_VERSION", "")
        ).strip()
        self._pull_request_cache: dict | None = None
        self._files_cache: list[dict] | None = None

    def set_pull_request_number(
        self,
        pull_request_number: int,
    ) -> None:
        self.pull_request_number = pull_request_number
        self._pull_request_cache = None
        self._files_cache = None

    def _require_pull_request_number(
        self,
    ) -> int:
        if self.pull_request_number is None:
            raise RuntimeError(
                "No pull request has been selected yet."
            )

        return self.pull_request_number

    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        }

        if self.api_version:
            headers["X-GitHub-Api-Version"] = self.api_version

        return headers

    def _api_get(
        self,
        url: str,
        params: dict | None = None,
    ) -> requests.Response:
        return requests.get(
            url,
            headers=self._headers(),
            params=params,
            timeout=30,
        )

    def _api_post(
        self,
        url: str,
        payload: dict,
    ) -> requests.Response:
        return requests.post(
            url,
            headers=self._headers(),
            json=payload,
            timeout=30,
        )

    def _api_patch(
        self,
        url: str,
        payload: dict,
    ) -> requests.Response:
        return requests.patch(
            url,
            headers=self._headers(),
            json=payload,
            timeout=30,
        )

    def _is_supported_file(
        self,
        filepath: str,
    ) -> bool:
        path = Path(filepath)

        if path.name in SUPPORTED_FILENAMES:
            return True

        return path.suffix.lower() in SUPPORTED_EXTENSIONS

    def _safe_path(
        self,
        filepath: str,
    ) -> bool:
        return ".." not in Path(filepath).parts

    def get_pull_request(
        self,
        force_refresh: bool = False,
    ) -> dict:
        if self._pull_request_cache is not None and not force_refresh:
            return self._pull_request_cache

        pull_request_number = self._require_pull_request_number()

        print("\nTOOL: get_pull_request()")

        url = (
            f"{GITHUB_API_URL}/repos/"
            f"{self.owner}/{self.repo}/pulls/"
            f"{pull_request_number}"
        )

        response = self._api_get(url)

        if response.status_code != 200:
            return {
                "error": (
                    f"GitHub returned {response.status_code}: "
                    f"{response.text}"
                )
            }

        data = response.json()
        head_repo = data["head"]["repo"] or {}
        base_repo = data["base"]["repo"] or {}

        result = {
            "number": data["number"],
            "title": data["title"],
            "description": data.get("body") or "",
            "state": data["state"],
            "html_url": data.get("html_url", ""),
            "author": data["user"]["login"],
            "source_branch": data["head"]["ref"],
            "source_sha": data["head"]["sha"],
            "source_repo_full_name": head_repo.get("full_name", ""),
            "source_repo_clone_url": head_repo.get("clone_url", ""),
            "source_repo_ssh_url": head_repo.get("ssh_url", ""),
            "target_branch": data["base"]["ref"],
            "target_sha": data["base"]["sha"],
            "target_repo_full_name": base_repo.get("full_name", ""),
            "commits": data["commits"],
            "changed_files": data["changed_files"],
            "additions": data["additions"],
            "deletions": data["deletions"],
            "is_fork": (
                head_repo.get("full_name")
                != base_repo.get("full_name")
            ),
        }

        self._pull_request_cache = result
        return result

    def list_open_pull_requests(
        self,
        state: str = "open",
    ) -> list[dict]:
        print(f"\nTOOL: list_open_pull_requests(state={state})")

        url = (
            f"{GITHUB_API_URL}/repos/"
            f"{self.owner}/{self.repo}/pulls"
        )

        open_pull_requests: list[dict] = []
        page = 1

        while True:
            response = self._api_get(
                url,
                params={
                    "state": state,
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": 100,
                    "page": page,
                },
            )

            if response.status_code != 200:
                return [
                    {
                        "error": (
                            f"GitHub returned {response.status_code}: "
                            f"{response.text}"
                        )
                    }
                ]

            pull_requests = response.json()

            if not pull_requests:
                break

            for data in pull_requests:
                head_repo = data["head"]["repo"] or {}
                base_repo = data["base"]["repo"] or {}

                open_pull_requests.append(
                    {
                        "number": data["number"],
                        "title": data["title"],
                        "draft": data.get("draft", False),
                        "html_url": data.get("html_url", ""),
                        "author": data["user"]["login"],
                        "source_branch": data["head"]["ref"],
                        "source_sha": data["head"]["sha"],
                        "source_repo_full_name": head_repo.get(
                            "full_name",
                            "",
                        ),
                        "target_branch": data["base"]["ref"],
                        "target_repo_full_name": base_repo.get(
                            "full_name",
                            "",
                        ),
                        "updated_at": data.get("updated_at", ""),
                    }
                )

            if len(pull_requests) < 100:
                break

            page += 1

        return open_pull_requests

    def get_pull_request_files(
        self,
        force_refresh: bool = False,
    ) -> list[dict]:
        if self._files_cache is not None and not force_refresh:
            return self._files_cache

        pull_request_number = self._require_pull_request_number()

        print("\nTOOL: get_pull_request_files()")

        url = (
            f"{GITHUB_API_URL}/repos/"
            f"{self.owner}/{self.repo}/pulls/"
            f"{pull_request_number}/files"
        )

        reviewable_files: list[dict] = []
        page = 1

        while True:
            response = self._api_get(
                url,
                params={
                    "per_page": 100,
                    "page": page,
                },
            )

            if response.status_code != 200:
                return [
                    {
                        "error": (
                            f"GitHub returned {response.status_code}: "
                            f"{response.text}"
                        )
                    }
                ]

            files = response.json()

            if not files:
                break

            for file_data in files:
                filename = file_data["filename"]

                if not self._is_supported_file(filename):
                    continue

                reviewable_files.append(
                    {
                        "filename": filename,
                        "status": file_data["status"],
                        "additions": file_data["additions"],
                        "deletions": file_data["deletions"],
                        "changes": file_data["changes"],
                        "patch": file_data.get("patch", ""),
                        "previous_filename": file_data.get(
                            "previous_filename"
                        ),
                    }
                )

            if len(files) < 100:
                break

            page += 1

        print(f"\nReviewable files: {len(reviewable_files)}")

        self._files_cache = reviewable_files
        return reviewable_files

    def get_file_diff(
        self,
        filepath: str,
    ) -> str:
        print(f"\nTOOL: get_file_diff({filepath})")

        files = self.get_pull_request_files()

        for file_data in files:
            if file_data.get("filename") == filepath:
                return file_data.get("patch", "")

        return ""

    def read_repository_file(
        self,
        filepath: str,
    ) -> str:
        print(f"\nTOOL: read_repository_file({filepath})")

        if not self._safe_path(filepath):
            return "Access denied: invalid file path."

        pr = self.get_pull_request()

        if "error" in pr:
            return pr["error"]

        repo_full_name = (
            pr.get("source_repo_full_name")
            or f"{self.owner}/{self.repo}"
        )

        url = (
            f"{GITHUB_API_URL}/repos/"
            f"{repo_full_name}/contents/"
            f"{filepath}"
        )

        response = self._api_get(
            url,
            params={
                "ref": pr["source_sha"],
            },
        )

        if response.status_code != 200:
            return (
                f"Unable to read {filepath}. "
                f"Status: {response.status_code}"
            )

        data = response.json()
        download_url = data.get("download_url")

        if not download_url:
            return f"No download URL for {filepath}"

        file_response = requests.get(
            download_url,
            headers=self._headers(),
            timeout=30,
        )

        if file_response.status_code != 200:
            return f"Unable to download {filepath}"

        return file_response.text

    def get_valid_diff_lines(
        self,
        filepath: str,
    ) -> set[int]:
        patch = self.get_file_diff(filepath)

        if not patch:
            return set()

        valid_lines: set[int] = set()
        new_line_number = None

        for line in patch.splitlines():
            if line.startswith("@@"):
                match = re.search(r"\+(\d+)", line)

                if match:
                    new_line_number = int(match.group(1))

                continue

            if new_line_number is None:
                continue

            if line.startswith("-") and not line.startswith("---"):
                continue

            if line.startswith("+") and not line.startswith("+++"):
                valid_lines.add(new_line_number)
                new_line_number += 1
                continue

            if not line.startswith("\\"):
                valid_lines.add(new_line_number)
                new_line_number += 1

        return valid_lines

    def post_inline_review_comment(
        self,
        filepath: str,
        line_number: int,
        comment_body: str,
    ) -> bool:
        print(
            "\nACTION: post_inline_review_comment("
            f"{filepath}:{line_number})"
        )

        valid_lines = self.get_valid_diff_lines(filepath)

        if line_number not in valid_lines:
            print(
                "Skipping inline comment because the line is not "
                "part of the PR diff."
            )
            return False

        pr = self.get_pull_request()

        if "error" in pr:
            print("Unable to get PR commit SHA.")
            return False

        url = (
            f"{GITHUB_API_URL}/repos/"
            f"{self.owner}/{self.repo}/pulls/"
            f"{self._require_pull_request_number()}/comments"
        )

        payload = {
            "body": comment_body,
            "commit_id": pr["source_sha"],
            "path": filepath,
            "line": line_number,
            "side": "RIGHT",
        }

        response = self._api_post(url, payload)

        if response.status_code == 201:
            print("Inline comment posted.")
            return True

        print(
            "Failed to post inline comment. "
            f"Status: {response.status_code}"
        )
        print(response.text)
        return False

    def post_pull_request_comment(
        self,
        comment_body: str,
        comment_label: str = "PR comment",
    ) -> bool:
        print("\nACTION: post_pull_request_comment()")

        url = (
            f"{GITHUB_API_URL}/repos/"
            f"{self.owner}/{self.repo}/issues/"
            f"{self._require_pull_request_number()}/comments"
        )

        response = self._api_post(
            url,
            {
                "body": comment_body,
            },
        )

        if response.status_code == 201:
            print(f"{comment_label} posted.")
            return True

        print(
            f"Failed to post {comment_label.lower()}. "
            f"Status: {response.status_code}"
        )
        print(response.text)
        return False

    def list_pull_request_comments(
        self,
    ) -> list[dict]:
        print("\nTOOL: list_pull_request_comments()")

        url = (
            f"{GITHUB_API_URL}/repos/"
            f"{self.owner}/{self.repo}/issues/"
            f"{self._require_pull_request_number()}/comments"
        )

        return self._get_paginated(url)

    def list_pull_request_review_comments(
        self,
    ) -> list[dict]:
        print("\nTOOL: list_pull_request_review_comments()")

        url = (
            f"{GITHUB_API_URL}/repos/"
            f"{self.owner}/{self.repo}/pulls/"
            f"{self._require_pull_request_number()}/comments"
        )

        return self._get_paginated(url)

    def get_existing_inline_comment_markers(
        self,
        marker_prefix: str | list[str],
    ) -> set[str]:
        marker_prefixes = (
            [marker_prefix]
            if isinstance(marker_prefix, str)
            else marker_prefix
        )
        markers: set[str] = set()

        for comment in self.list_pull_request_review_comments():
            body = comment.get("body") or ""

            for match in re.finditer(
                r"<!--\s*([^>]+?)\s*-->",
                body,
            ):
                marker = match.group(1).strip()

                if any(
                    marker.startswith(prefix)
                    for prefix in marker_prefixes
                ):
                    markers.add(marker)

        return markers

    def upsert_pull_request_comment(
        self,
        marker: str | list[str],
        comment_body: str,
        comment_label: str = "PR comment",
    ) -> bool:
        print("\nACTION: upsert_pull_request_comment()")
        markers = [marker] if isinstance(marker, str) else marker

        for comment in self.list_pull_request_comments():
            body = comment.get("body") or ""

            if not any(item in body for item in markers):
                continue

            comment_id = comment.get("id")

            if comment_id is None:
                continue

            url = (
                f"{GITHUB_API_URL}/repos/"
                f"{self.owner}/{self.repo}/issues/"
                f"comments/{comment_id}"
            )

            response = self._api_patch(
                url,
                {
                    "body": comment_body,
                },
            )

            if response.status_code == 200:
                print(f"Existing {comment_label.lower()} updated.")
                return True

            print(
                f"Failed to update {comment_label.lower()}. "
                f"Status: {response.status_code}"
            )
            print(response.text)
            return False

        return self.post_pull_request_comment(
            comment_body,
            comment_label=comment_label,
        )

    def _get_paginated(
        self,
        url: str,
        params: dict | None = None,
    ) -> list[dict]:
        results: list[dict] = []
        page = 1

        while True:
            page_params = {
                **(params or {}),
                "per_page": 100,
                "page": page,
            }

            response = self._api_get(
                url,
                params=page_params,
            )

            if response.status_code != 200:
                return [
                    {
                        "error": (
                            f"GitHub returned {response.status_code}: "
                            f"{response.text}"
                        )
                    }
                ]

            items = response.json()

            if not items:
                break

            results.extend(items)

            if len(items) < 100:
                break

            page += 1

        return results

    def _authenticated_clone_url(
        self,
        clone_url: str,
    ) -> str:
        if not clone_url.startswith("https://"):
            return clone_url

        token = quote(self.token, safe="")
        return clone_url.replace(
            "https://",
            f"https://x-access-token:{token}@",
            1,
        )

    def _run_git(
        self,
        args: list[str],
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=300,
        )

    @contextmanager
    def checkout_pull_request_head(
        self,
    ) -> Iterator[PullRequestCheckout]:
        pr = self.get_pull_request()

        if "error" in pr:
            raise RuntimeError(pr["error"])

        clone_url = pr["source_repo_clone_url"]

        if not clone_url:
            raise RuntimeError("GitHub did not return a clone URL.")

        authenticated_url = self._authenticated_clone_url(clone_url)

        with tempfile.TemporaryDirectory(
            prefix="pr-review-v3-",
        ) as temp_dir:
            checkout_path = Path(temp_dir) / self.repo

            print(
                "\nCloning PR head repository into temporary workspace..."
            )

            clone_command = [
                "git",
                "clone",
                "--no-tags",
                "--filter=blob:none",
                "--depth",
                "50",
                "--branch",
                pr["source_branch"],
                authenticated_url,
                str(checkout_path),
            ]

            clone_result = self._run_git(
                clone_command
            )

            if clone_result.returncode != 0:
                print(
                    "Source branch clone was not available; attempting "
                    "default clone and direct SHA fetch."
                )
                clone_result = self._run_git(
                    [
                        "git",
                        "clone",
                        "--no-tags",
                        "--filter=blob:none",
                        "--depth",
                        "50",
                        authenticated_url,
                        str(checkout_path),
                    ]
                )

                if clone_result.returncode != 0:
                    raise RuntimeError(
                        "Git clone failed:\n"
                        f"{clone_result.stderr}"
                    )

            fetch_result = self._run_git(
                [
                    "git",
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    pr["source_sha"],
                ],
                cwd=checkout_path,
            )

            if fetch_result.returncode != 0:
                print(
                    "Direct SHA fetch was not available; attempting "
                    "checkout from cloned refs."
                )

            checkout_result = self._run_git(
                [
                    "git",
                    "checkout",
                    "--detach",
                    pr["source_sha"],
                ],
                cwd=checkout_path,
            )

            if checkout_result.returncode != 0:
                raise RuntimeError(
                    "Git checkout failed:\n"
                    f"{checkout_result.stderr}"
                )

            yield PullRequestCheckout(
                path=checkout_path,
                source_sha=pr["source_sha"],
                source_repo=pr["source_repo_full_name"],
            )
