from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Recommendation(str, Enum):
    APPROVE = "APPROVE"
    APPROVE_WITH_COMMENTS = "APPROVE_WITH_COMMENTS"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class FindingSource(str, Enum):
    STATIC = "STATIC"
    BUILD = "BUILD"
    TEST = "TEST"
    GEMINI = "GEMINI"


class ReviewFinding(BaseModel):
    severity: Severity

    category: str

    file: str

    line_number: Optional[int] = None

    title: str

    explanation: str

    why_it_matters: str

    suggested_fix: str

    source: FindingSource = FindingSource.GEMINI


class PRReviewResult(BaseModel):
    pr_summary: str

    overall_risk: Severity

    files_reviewed: list[str] = Field(
        default_factory=list
    )

    findings: list[ReviewFinding] = Field(
        default_factory=list
    )

    testing_recommendations: list[str] = Field(
        default_factory=list
    )

    final_recommendation: Recommendation


class BuildRunSummary(BaseModel):
    command: str

    success: bool

    exit_code: int

    duration_seconds: float

    output_excerpt: str = ""


class XcodeTarget(BaseModel):
    container_type: str

    container_path: str

    scheme: str


class XcodeAnalysisResult(BaseModel):
    xcodebuild_available: bool

    targets: list[XcodeTarget] = Field(
        default_factory=list
    )

    commands: list[BuildRunSummary] = Field(
        default_factory=list
    )

    findings: list[ReviewFinding] = Field(
        default_factory=list
    )

    summary: str = ""
