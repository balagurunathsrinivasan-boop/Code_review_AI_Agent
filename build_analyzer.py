from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from review_models import BuildRunSummary
from review_models import FindingSource
from review_models import ReviewFinding
from review_models import Severity
from review_models import XcodeAnalysisResult
from review_models import XcodeTarget


DIAGNOSTIC_EXTENSIONS = (
    "swift",
    "m",
    "mm",
    "h",
    "c",
    "cpp",
    "cc",
    "hpp",
)


DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>.*?\.(?:"
    + "|".join(DIAGNOSTIC_EXTENSIONS)
    + r")):(?P<line>\d+):(?:(?P<column>\d+):)?\s*"
    r"(?P<kind>error|warning):\s*(?P<message>.+)$"
)


@dataclass(frozen=True)
class XcodeContainer:
    container_type: str
    path: Path


@dataclass(frozen=True)
class CommandRun:
    summary: BuildRunSummary
    output: str
    timed_out: bool


class XcodeBuildAnalyzer:
    def __init__(
        self,
        repo_path: Path,
        timeout_seconds: int = 1800,
        max_schemes: int = 3,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.timeout_seconds = int(
            os.getenv(
                "XCODEBUILD_TIMEOUT_SECONDS",
                str(timeout_seconds),
            )
        )
        self.max_schemes = int(
            os.getenv(
                "XCODEBUILD_MAX_SCHEMES",
                str(max_schemes),
            )
        )
        self.destination = os.getenv(
            "XCODEBUILD_DESTINATION",
            "",
        ).strip()
        self.only_scheme = os.getenv(
            "XCODEBUILD_SCHEME",
            "",
        ).strip()
        self.only_container = os.getenv(
            "XCODEBUILD_CONTAINER",
            "",
        ).strip()
        self.skip_tests = (
            os.getenv("XCODEBUILD_SKIP_TESTS", "0") == "1"
        )
        self.allow_test_without_destination = (
            os.getenv(
                "XCODEBUILD_ALLOW_TEST_WITHOUT_DESTINATION",
                "0",
            )
            == "1"
        )
        self.extra_args = shlex.split(
            os.getenv(
                "XCODEBUILD_EXTRA_ARGS",
                "",
            )
        )
        self._destination_cache: dict[tuple[str, str], str] = {}

    def analyze(
        self,
    ) -> XcodeAnalysisResult:
        if not shutil.which("xcodebuild"):
            return XcodeAnalysisResult(
                xcodebuild_available=False,
                summary=(
                    "xcodebuild is not available on this machine, so "
                    "build and test analysis was skipped."
                ),
            )

        containers = self.discover_containers()

        if not containers:
            return XcodeAnalysisResult(
                xcodebuild_available=True,
                summary=(
                    "No .xcworkspace or .xcodeproj containers were found, "
                    "so xcodebuild analysis was skipped."
                ),
            )

        targets = self.discover_targets(containers)

        if not targets:
            return XcodeAnalysisResult(
                xcodebuild_available=True,
                summary=(
                    "Xcode containers were found, but no shared schemes "
                    "could be discovered."
                ),
            )

        selected_targets = targets[: self.max_schemes]
        result = XcodeAnalysisResult(
            xcodebuild_available=True,
            targets=selected_targets,
            summary=(
                f"Discovered {len(targets)} scheme(s); running "
                f"{len(selected_targets)} based on current configuration."
            ),
        )

        for target in selected_targets:
            destination = self.destination or self.infer_destination(target)

            build_run = self.run_action(
                target,
                "build",
                destination,
            )
            result.commands.append(build_run.summary)

            if not build_run.summary.success:
                result.findings.extend(
                    self.findings_from_output(
                        target=target,
                        action="build",
                        output=build_run.output,
                        timed_out=build_run.timed_out,
                    )
                )
                continue

            if self.skip_tests:
                continue

            if (
                not destination
                and not self.allow_test_without_destination
            ):
                continue

            test_run = self.run_action(
                target,
                "test",
                destination,
            )
            result.commands.append(test_run.summary)

            if not test_run.summary.success:
                result.findings.extend(
                    self.findings_from_output(
                        target=target,
                        action="test",
                        output=test_run.output,
                        timed_out=test_run.timed_out,
                    )
                )

        if result.findings:
            result.summary += (
                f" xcodebuild produced {len(result.findings)} "
                "structured finding(s)."
            )

        return result

    def discover_containers(
        self,
    ) -> list[XcodeContainer]:
        if self.only_container:
            configured = (self.repo_path / self.only_container).resolve()

            if configured.suffix == ".xcworkspace":
                return [
                    XcodeContainer("workspace", configured)
                ]

            if configured.suffix == ".xcodeproj":
                return [
                    XcodeContainer("project", configured)
                ]

            return []

        workspaces = [
            XcodeContainer("workspace", path)
            for path in self._find_paths("*.xcworkspace")
        ]

        if workspaces:
            return workspaces

        return [
            XcodeContainer("project", path)
            for path in self._find_paths("*.xcodeproj")
        ]

    def _find_paths(
        self,
        pattern: str,
    ) -> list[Path]:
        ignored_parts = {
            ".git",
            ".build",
            "DerivedData",
            "node_modules",
        }

        paths = []

        for path in self.repo_path.rglob(pattern):
            relative_parts = set(
                path.relative_to(self.repo_path).parts
            )

            if relative_parts.intersection(ignored_parts):
                continue

            paths.append(path)

        return sorted(paths)

    def discover_targets(
        self,
        containers: list[XcodeContainer],
    ) -> list[XcodeTarget]:
        targets: list[XcodeTarget] = []

        for container in containers:
            schemes = self._schemes_for_container(container)

            for scheme in schemes:
                if self.only_scheme and scheme != self.only_scheme:
                    continue

                targets.append(
                    XcodeTarget(
                        container_type=container.container_type,
                        container_path=self._relative_path(
                            container.path,
                        ),
                        scheme=scheme,
                    )
                )

        return targets

    def _schemes_for_container(
        self,
        container: XcodeContainer,
    ) -> list[str]:
        command = [
            "xcodebuild",
            "-list",
            "-json",
            *self._container_args(container),
        ]

        run = self._run_raw(
            command,
            timeout_seconds=120,
        )

        if run.summary.success:
            try:
                data = json.loads(run.output)
            except json.JSONDecodeError:
                data = {}

            payload = (
                data.get("workspace")
                or data.get("project")
                or {}
            )

            schemes = payload.get("schemes", [])

            if isinstance(schemes, list):
                return [
                    str(scheme)
                    for scheme in schemes
                    if str(scheme).strip()
                ]

        return self._schemes_from_plain_list(container)

    def _schemes_from_plain_list(
        self,
        container: XcodeContainer,
    ) -> list[str]:
        command = [
            "xcodebuild",
            "-list",
            *self._container_args(container),
        ]

        run = self._run_raw(
            command,
            timeout_seconds=120,
        )

        if not run.summary.success:
            return []

        schemes: list[str] = []
        in_schemes = False

        for line in run.output.splitlines():
            stripped = line.strip()

            if stripped == "Schemes:":
                in_schemes = True
                continue

            if in_schemes and stripped.endswith(":"):
                break

            if in_schemes and stripped:
                schemes.append(stripped)

        return schemes

    def infer_destination(
        self,
        target: XcodeTarget,
    ) -> str:
        cache_key = (
            target.container_path,
            target.scheme,
        )

        if cache_key in self._destination_cache:
            return self._destination_cache[cache_key]

        container = XcodeContainer(
            target.container_type,
            self.repo_path / target.container_path,
        )

        command = [
            "xcodebuild",
            *self._container_args(container),
            "-scheme",
            target.scheme,
            "-showdestinations",
        ]

        run = self._run_raw(
            command,
            timeout_seconds=120,
        )

        destination = ""

        if run.summary.success:
            destination = self._parse_destination(run.output)

        self._destination_cache[cache_key] = destination
        return destination

    def _parse_destination(
        self,
        output: str,
    ) -> str:
        candidates = []

        for line in output.splitlines():
            stripped = line.strip()

            if not (
                stripped.startswith("{")
                and stripped.endswith("}")
            ):
                continue

            if "placeholder" in stripped.lower():
                continue

            fields = self._destination_fields(stripped)
            platform = fields.get("platform", "")
            identifier = fields.get("id", "")

            if platform == "macOS":
                return "platform=macOS"

            if identifier and (
                "Simulator" in platform
                or platform in {"iOS", "tvOS", "watchOS"}
            ):
                candidates.append(f"id={identifier}")

        return candidates[0] if candidates else ""

    def _destination_fields(
        self,
        destination_line: str,
    ) -> dict[str, str]:
        content = destination_line.strip("{} ")
        fields: dict[str, str] = {}

        for part in content.split(","):
            if ":" not in part:
                continue

            key, value = part.split(":", 1)
            fields[key.strip()] = value.strip()

        return fields

    def run_action(
        self,
        target: XcodeTarget,
        action: str,
        destination: str,
    ) -> CommandRun:
        container = XcodeContainer(
            target.container_type,
            self.repo_path / target.container_path,
        )

        derived_data_path = (
            self.repo_path
            / ".pr-review-derived-data"
            / self._safe_name(target.scheme)
        )

        command = [
            "xcodebuild",
            *self._container_args(container),
            "-scheme",
            target.scheme,
            "-derivedDataPath",
            str(derived_data_path),
        ]

        if destination:
            command.extend(
                [
                    "-destination",
                    destination,
                ]
            )

        command.extend(self.extra_args)
        command.append(action)

        return self._run_raw(
            command,
            timeout_seconds=self.timeout_seconds,
        )

    def _container_args(
        self,
        container: XcodeContainer,
    ) -> list[str]:
        if container.container_type == "workspace":
            return [
                "-workspace",
                str(container.path),
            ]

        return [
            "-project",
            str(container.path),
        ]

    def _run_raw(
        self,
        command: list[str],
        timeout_seconds: int,
    ) -> CommandRun:
        start = time.monotonic()

        try:
            completed = subprocess.run(
                command,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            output = (
                (completed.stdout or "")
                + "\n"
                + (completed.stderr or "")
            ).strip()
            timed_out = False
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or ""
            stderr = error.stderr or ""

            if isinstance(stdout, bytes):
                stdout = stdout.decode(
                    "utf-8",
                    errors="replace",
                )

            if isinstance(stderr, bytes):
                stderr = stderr.decode(
                    "utf-8",
                    errors="replace",
                )

            output = (stdout + "\n" + stderr).strip()
            timed_out = True
            exit_code = 124

        duration = time.monotonic() - start

        summary = BuildRunSummary(
            command=self._format_command(command),
            success=(exit_code == 0 and not timed_out),
            exit_code=exit_code,
            duration_seconds=round(duration, 2),
            output_excerpt=self._excerpt(output),
        )

        return CommandRun(
            summary=summary,
            output=output,
            timed_out=timed_out,
        )

    def findings_from_output(
        self,
        *,
        target: XcodeTarget,
        action: str,
        output: str,
        timed_out: bool,
    ) -> list[ReviewFinding]:
        source = (
            FindingSource.TEST
            if action == "test"
            else FindingSource.BUILD
        )
        category = "Tests" if action == "test" else "Build"
        findings: list[ReviewFinding] = []
        seen: set[tuple[str, int | None, str]] = set()

        for line in output.splitlines():
            match = DIAGNOSTIC_RE.match(line.strip())

            if not match:
                continue

            kind = match.group("kind")
            message = match.group("message").strip()
            file_path = self._normalize_reported_file(
                match.group("file")
            )
            line_number = int(match.group("line"))
            key = (
                file_path,
                line_number,
                message.lower(),
            )

            if key in seen:
                continue

            seen.add(key)

            findings.append(
                ReviewFinding(
                    severity=(
                        Severity.HIGH
                        if kind == "error"
                        else Severity.MEDIUM
                    ),
                    category=category,
                    file=file_path,
                    line_number=line_number,
                    title=(
                        "xcodebuild test failure"
                        if source == FindingSource.TEST
                        else "xcodebuild build failure"
                    ),
                    explanation=message,
                    why_it_matters=(
                        "The checked-out PR head does not pass the "
                        f"`xcodebuild {action}` command for scheme "
                        f"`{target.scheme}`."
                    ),
                    suggested_fix=(
                        "Fix the compiler or test failure locally, then "
                        "rerun the V3 reviewer."
                    ),
                    source=source,
                )
            )

            if len(findings) >= 20:
                break

        if findings:
            return findings

        title = (
            "xcodebuild command timed out"
            if timed_out
            else f"xcodebuild {action} failed"
        )

        return [
            ReviewFinding(
                severity=Severity.HIGH,
                category=category,
                file=target.container_path,
                line_number=None,
                title=title,
                explanation=self._failure_summary(
                    output,
                    action,
                    target,
                ),
                why_it_matters=(
                    "The PR cannot be considered build/test clean until "
                    "this deterministic command succeeds."
                ),
                suggested_fix=(
                    "Run the displayed xcodebuild command locally and fix "
                    "the first reported error."
                ),
                source=source,
            )
        ]

    def _failure_summary(
        self,
        output: str,
        action: str,
        target: XcodeTarget,
    ) -> str:
        important_lines = []

        for line in output.splitlines():
            lowered = line.lower()

            if (
                " error:" in lowered
                or " failed" in lowered
                or "testing failed" in lowered
                or "build failed" in lowered
            ):
                important_lines.append(line.strip())

            if len(important_lines) >= 8:
                break

        if important_lines:
            return "\n".join(important_lines)

        return (
            f"`xcodebuild {action}` failed for scheme "
            f"`{target.scheme}`. No file-specific diagnostic could be "
            "extracted from the captured output."
        )

    def _normalize_reported_file(
        self,
        reported_file: str,
    ) -> str:
        path = Path(reported_file)

        if not path.is_absolute():
            return reported_file

        try:
            return str(path.resolve().relative_to(self.repo_path))
        except ValueError:
            return path.name

    def _relative_path(
        self,
        path: Path,
    ) -> str:
        try:
            return str(path.resolve().relative_to(self.repo_path))
        except ValueError:
            return str(path)

    def _safe_name(
        self,
        value: str,
    ) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)

    def _format_command(
        self,
        command: list[str],
    ) -> str:
        return " ".join(
            shlex.quote(part)
            for part in command
        )

    def _excerpt(
        self,
        output: str,
        limit: int = 6000,
    ) -> str:
        output = output.strip()

        if len(output) <= limit:
            return output

        return output[-limit:]
