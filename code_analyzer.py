from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from review_models import FindingSource
from review_models import ReviewFinding
from review_models import Severity


class CodeAnalyzer:
    def analyze(
        self,
        filepath: str,
        content: str,
        allowed_lines: Iterable[int] | None = None,
    ) -> list[ReviewFinding]:
        line_filter = (
            set(allowed_lines)
            if allowed_lines is not None
            else None
        )

        findings: list[ReviewFinding] = []
        suffix = Path(filepath).suffix.lower()

        for line_number, line in enumerate(
            content.splitlines(),
            start=1,
        ):
            if line_filter is not None and line_number not in line_filter:
                continue

            stripped = line.strip()

            if not stripped:
                continue

            if self._is_comment_only_line(stripped, suffix):
                continue

            findings.extend(
                self._analyze_generic_line(
                    filepath,
                    line_number,
                    stripped,
                )
            )

            if suffix == ".swift":
                findings.extend(
                    self._analyze_swift_line(
                        filepath,
                        line_number,
                        stripped,
                    )
                )
            elif suffix in {".kt", ".kts"}:
                findings.extend(
                    self._analyze_kotlin_line(
                        filepath,
                        line_number,
                        stripped,
                    )
                )
            elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
                findings.extend(
                    self._analyze_javascript_line(
                        filepath,
                        line_number,
                        stripped,
                    )
                )
            elif suffix == ".py":
                findings.extend(
                    self._analyze_python_line(
                        filepath,
                        line_number,
                        stripped,
                    )
                )

        return findings

    def _is_comment_only_line(
        self,
        stripped: str,
        suffix: str,
    ) -> bool:
        if suffix in {".swift", ".kt", ".kts", ".java", ".js", ".ts"}:
            return stripped.startswith("//")

        if suffix in {".py", ".rb", ".sh", ".bash", ".zsh"}:
            return stripped.startswith("#")

        return False

    def _finding(
        self,
        *,
        severity: Severity,
        category: str,
        file: str,
        line_number: int,
        title: str,
        explanation: str,
        why_it_matters: str,
        suggested_fix: str,
    ) -> ReviewFinding:
        return ReviewFinding(
            severity=severity,
            category=category,
            file=file,
            line_number=line_number,
            title=title,
            explanation=explanation,
            why_it_matters=why_it_matters,
            suggested_fix=suggested_fix,
            source=FindingSource.STATIC,
        )

    def _analyze_generic_line(
        self,
        filepath: str,
        line_number: int,
        line: str,
    ) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []

        secret_pattern = re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password)\b"
            r"\s*[:=]\s*['\"][^'\"]{8,}['\"]"
        )

        if secret_pattern.search(line):
            findings.append(
                self._finding(
                    severity=Severity.CRITICAL,
                    category="Security",
                    file=filepath,
                    line_number=line_number,
                    title="Possible hard-coded secret",
                    explanation=(
                        "This line appears to assign a credential-like "
                        "value directly in source code."
                    ),
                    why_it_matters=(
                        "Secrets committed to source control can be copied, "
                        "logged, or exposed through forks and build systems."
                    ),
                    suggested_fix=(
                        "Move the value to a secure secret store or runtime "
                        "configuration, then rotate the exposed credential."
                    ),
                )
            )

        return findings

    def _analyze_swift_line(
        self,
        filepath: str,
        line_number: int,
        line: str,
    ) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []

        if re.search(r"\btry!", line):
            findings.append(
                self._finding(
                    severity=Severity.HIGH,
                    category="Crash Risk",
                    file=filepath,
                    line_number=line_number,
                    title="Forced try can crash at runtime",
                    explanation=(
                        "The newly inspected Swift line uses `try!`, which "
                        "turns any thrown error into a runtime crash."
                    ),
                    why_it_matters=(
                        "A recoverable failure path can terminate the app "
                        "instead of being surfaced to the user or caller."
                    ),
                    suggested_fix=(
                        "Use `do`/`catch`, `try?`, or propagate the error "
                        "with `throws` so failure is handled explicitly."
                    ),
                )
            )

        if re.search(r"\bas!", line):
            findings.append(
                self._finding(
                    severity=Severity.HIGH,
                    category="Crash Risk",
                    file=filepath,
                    line_number=line_number,
                    title="Forced cast can crash at runtime",
                    explanation=(
                        "The line uses `as!`, which traps if the value is "
                        "not of the expected type."
                    ),
                    why_it_matters=(
                        "Unexpected data or framework behavior can turn this "
                        "cast into a production crash."
                    ),
                    suggested_fix=(
                        "Use `as?` with a guarded fallback, or redesign the "
                        "API boundary so the type is guaranteed."
                    ),
                )
            )

        if re.search(r"\bfatalError\s*\(", line):
            findings.append(
                self._finding(
                    severity=Severity.HIGH,
                    category="Crash Risk",
                    file=filepath,
                    line_number=line_number,
                    title="fatalError reaches production code",
                    explanation=(
                        "This line calls `fatalError`, which immediately "
                        "terminates the process when reached."
                    ),
                    why_it_matters=(
                        "A reachable fatal path can crash users instead of "
                        "failing in a controlled way."
                    ),
                    suggested_fix=(
                        "Replace it with explicit error handling or confine "
                        "it to unreachable test-only scaffolding."
                    ),
                )
            )

        if re.search(r"\bpreconditionFailure\s*\(", line):
            findings.append(
                self._finding(
                    severity=Severity.HIGH,
                    category="Crash Risk",
                    file=filepath,
                    line_number=line_number,
                    title="preconditionFailure can terminate the app",
                    explanation=(
                        "This line calls `preconditionFailure`, which traps "
                        "when the code path is executed."
                    ),
                    why_it_matters=(
                        "If external data or user flow reaches this branch, "
                        "the app will terminate instead of recovering."
                    ),
                    suggested_fix=(
                        "Handle the invalid state explicitly or return a "
                        "typed failure to the caller."
                    ),
                )
            )

        if self._looks_like_force_unwrap(line):
            findings.append(
                self._finding(
                    severity=Severity.MEDIUM,
                    category="Crash Risk",
                    file=filepath,
                    line_number=line_number,
                    title="Force unwrap should be justified",
                    explanation=(
                        "This line appears to force unwrap an optional value."
                    ),
                    why_it_matters=(
                        "If the optional is nil, Swift will trap at runtime."
                    ),
                    suggested_fix=(
                        "Use `guard let`, `if let`, nil coalescing, or make "
                        "the invariant explicit before force unwrapping."
                    ),
                )
            )

        if re.search(r"\bunowned\s+self\b", line):
            findings.append(
                self._finding(
                    severity=Severity.MEDIUM,
                    category="Memory",
                    file=filepath,
                    line_number=line_number,
                    title="unowned self can crash after deallocation",
                    explanation=(
                        "The closure captures `self` as unowned."
                    ),
                    why_it_matters=(
                        "If the closure outlives the object, accessing "
                        "`self` will trap."
                    ),
                    suggested_fix=(
                        "Prefer `[weak self]` unless the closure lifetime is "
                        "strictly shorter than the object lifetime."
                    ),
                )
            )

        if "Task.detached" in line:
            findings.append(
                self._finding(
                    severity=Severity.MEDIUM,
                    category="Concurrency",
                    file=filepath,
                    line_number=line_number,
                    title="Detached task bypasses structured concurrency",
                    explanation=(
                        "This line starts a detached task, which does not "
                        "inherit actor context, priority, or cancellation."
                    ),
                    why_it_matters=(
                        "Detached tasks can update state from the wrong actor "
                        "or continue running after the owning operation ends."
                    ),
                    suggested_fix=(
                        "Use a structured `Task`, an actor method, or pass "
                        "explicit context and cancellation handling."
                    ),
                )
            )

        if "DispatchQueue.main.sync" in line:
            findings.append(
                self._finding(
                    severity=Severity.HIGH,
                    category="Concurrency",
                    file=filepath,
                    line_number=line_number,
                    title="Synchronous dispatch to main can deadlock",
                    explanation=(
                        "This line synchronously dispatches work to the main "
                        "queue."
                    ),
                    why_it_matters=(
                        "If the current execution is already on the main "
                        "queue, this will deadlock."
                    ),
                    suggested_fix=(
                        "Use `DispatchQueue.main.async`, `@MainActor`, or "
                        "`await MainActor.run` depending on the call site."
                    ),
                )
            )

        return findings

    def _looks_like_force_unwrap(
        self,
        line: str,
    ) -> bool:
        if "!=" in line or "!!" in line:
            return False

        if re.search(r"\btry!", line) or re.search(r"\bas!", line):
            return False

        return bool(
            re.search(r"\b[A-Za-z_][A-Za-z0-9_]*!\s*[\.\)\],]", line)
        )

    def _analyze_kotlin_line(
        self,
        filepath: str,
        line_number: int,
        line: str,
    ) -> list[ReviewFinding]:
        if "!!" not in line:
            return []

        return [
            self._finding(
                severity=Severity.MEDIUM,
                category="Crash Risk",
                file=filepath,
                line_number=line_number,
                title="Non-null assertion can crash",
                explanation=(
                    "The Kotlin non-null assertion operator `!!` throws "
                    "if the value is null."
                ),
                why_it_matters=(
                    "Unexpected null data can crash the app instead of "
                    "being handled."
                ),
                suggested_fix=(
                    "Use a safe call, `let`, Elvis operator, or explicit "
                    "validation before accessing the value."
                ),
            )
        ]

    def _analyze_javascript_line(
        self,
        filepath: str,
        line_number: int,
        line: str,
    ) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []

        if re.search(r"\beval\s*\(", line):
            findings.append(
                self._finding(
                    severity=Severity.HIGH,
                    category="Security",
                    file=filepath,
                    line_number=line_number,
                    title="eval executes dynamic code",
                    explanation=(
                        "This line executes a string as code with `eval`."
                    ),
                    why_it_matters=(
                        "Dynamic code execution can create injection and "
                        "privilege escalation risks."
                    ),
                    suggested_fix=(
                        "Replace `eval` with structured parsing or a limited "
                        "command map."
                    ),
                )
            )

        if "dangerouslySetInnerHTML" in line:
            findings.append(
                self._finding(
                    severity=Severity.MEDIUM,
                    category="Security",
                    file=filepath,
                    line_number=line_number,
                    title="HTML injection path needs sanitization",
                    explanation=(
                        "This line uses `dangerouslySetInnerHTML`."
                    ),
                    why_it_matters=(
                        "Rendering unsanitized HTML can expose users to XSS."
                    ),
                    suggested_fix=(
                        "Only pass trusted sanitized HTML, or render the "
                        "content as text/components instead."
                    ),
                )
            )

        return findings

    def _analyze_python_line(
        self,
        filepath: str,
        line_number: int,
        line: str,
    ) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []

        if "shell=True" in line and "subprocess" in line:
            findings.append(
                self._finding(
                    severity=Severity.HIGH,
                    category="Security",
                    file=filepath,
                    line_number=line_number,
                    title="subprocess shell execution needs strict control",
                    explanation=(
                        "This line appears to run a subprocess through the "
                        "shell."
                    ),
                    why_it_matters=(
                        "Shell execution can become command injection when "
                        "any part of the command includes untrusted input."
                    ),
                    suggested_fix=(
                        "Pass arguments as a list with `shell=False`, or "
                        "validate and quote every untrusted value."
                    ),
                )
            )

        if re.search(r"\beval\s*\(", line):
            findings.append(
                self._finding(
                    severity=Severity.HIGH,
                    category="Security",
                    file=filepath,
                    line_number=line_number,
                    title="eval executes dynamic Python code",
                    explanation=(
                        "This line evaluates a string as Python code."
                    ),
                    why_it_matters=(
                        "Dynamic evaluation can execute attacker-controlled "
                        "code if untrusted input reaches this path."
                    ),
                    suggested_fix=(
                        "Use structured parsing, explicit dispatch tables, "
                        "or `ast.literal_eval` for trusted literals."
                    ),
                )
            )

        return findings
