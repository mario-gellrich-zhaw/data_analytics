"""Pydantic input/output schemas for every agent."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

PHASES = ["business_objectives", "define_data", "collect_data", "prepare_store",
          "eda", "modeling", "evaluation", "present_results"]


class AgentInput(BaseModel):
    """What every agent receives: a compact brief, never raw data."""
    run_id: str
    node: str
    objective: str
    region: Optional[str] = None
    objective_spec: Optional[dict] = None
    context: dict = Field(default_factory=dict, description="compact summaries of earlier phases")
    guidance: list[str] = Field(default_factory=list, description="critic / supervisor feedback for this visit")
    workspace_files: list[str] = Field(default_factory=list)
    offline: bool = False


# --- business_objectives -----------------------------------------------------
class ObjectiveSpec(BaseModel):
    title: str
    task_type: Literal["regression", "classification", "analytics"]
    target_variable: str = Field(description="what is predicted, in words (e.g. 'monthly gross rent in CHF')")
    target_column_hint: str = Field(description="suggested snake_case column name for the target")
    unit_of_analysis: str = Field(description="one row = ... (e.g. one rental listing)")
    primary_metric: Literal["mae", "rmse", "mape", "r2", "accuracy", "f1_macro", "roc_auc"]
    success_threshold: float = Field(description="value of primary_metric that counts as success (mape as a fraction, e.g. 0.15)")
    baseline: str = Field(description="naive baseline the model must beat, e.g. 'median rent of the training set'")
    region: Optional[str] = None
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    secondary_metrics: list[str] = Field(default_factory=list)


# --- define_data -------------------------------------------------------------
class FeatureRequirement(BaseModel):
    name: str
    description: str = ""
    required: bool = False


class SourceCandidate(BaseModel):
    name: str
    url: Optional[str] = None
    kind: str = Field("", description="open data portal | API | public dataset | enrichment")
    notes: str = ""


class DataRequirements(BaseModel):
    entity: str = Field(description="what one row represents")
    target_column: str
    granularity: str = ""
    features: list[FeatureRequirement]
    min_rows: int = Field(ge=10)
    candidate_sources: list[SourceCandidate] = Field(default_factory=list)
    enrichment_sources: list[SourceCandidate] = Field(default_factory=list)
    feasible: bool = True
    infeasibility_reason: str = ""
    notes: str = ""


# --- collect_data --------------------------------------------------------------
class CollectedFile(BaseModel):
    path: str
    description: str = ""
    rows_estimate: Optional[int] = None


class CollectionReport(BaseModel):
    files: list[CollectedFile]
    primary_file: str = Field(description="raw file that holds the main entity table")
    license_assessment: str
    quality_notes: str = ""
    obtainable: bool = True
    missing_requirements: list[str] = Field(default_factory=list)


# --- prepare_store ---------------------------------------------------------------
class PrepOutput(BaseModel):
    target_column: str
    feature_columns: list[str]
    row_count: int
    cleaning_steps: list[str]
    enrichments_joined: list[str] = Field(default_factory=list)
    known_issues: list[str] = Field(default_factory=list)
    needs_more_data: bool = False
    needs_more_data_reason: str = ""


# --- eda ---------------------------------------------------------------------------
class EDAOutput(BaseModel):
    key_findings: list[str]
    charts: list[str] = Field(default_factory=list)
    leakage_suspects: list[str] = Field(default_factory=list)
    feature_ideas: list[str] = Field(default_factory=list)
    data_issues: list[str] = Field(default_factory=list)
    recommend_prepare_changes: bool = False
    requested_changes: list[str] = Field(default_factory=list)


# --- modeling ----------------------------------------------------------------------
class ModelingOutput(BaseModel):
    best_model_name: str
    features_used: list[str]
    experiments_run: int
    validation_metrics: dict[str, float] = Field(default_factory=dict)
    approach: str = ""
    needs_features: bool = False
    requested_changes: list[str] = Field(default_factory=list)


# --- evaluation -------------------------------------------------------------------
class EvaluationOutput(BaseModel):
    objective_met: bool
    primary_metric_value: Optional[float] = None
    summary: str
    segment_errors: list[str] = Field(default_factory=list)
    explainability: list[str] = Field(default_factory=list)
    robustness: list[str] = Field(default_factory=list)
    recommendation: Literal["present", "modeling", "eda"]
    reasons: list[str] = Field(default_factory=list)


# --- present_results ------------------------------------------------------------------
class PresenterOutput(BaseModel):
    report_markdown_path: str = "report/final_report.md"
    headline: str


# --- control agents --------------------------------------------------------------------
class Critique(BaseModel):
    score: float = Field(ge=0, le=10)
    verdict: Literal["accept", "revise", "route_back"]
    issues: list[str] = Field(default_factory=list)
    suggested_route: Optional[str] = None
    feedback_for_agent: str = ""


class SupervisorDecision(BaseModel):
    next_node: str
    rationale: str
    guidance_for_next_agent: str = ""


class FileChange(BaseModel):
    path: str = Field(description="relative to the project root")
    kind: Literal["replace_file", "search_replace"]
    content: Optional[str] = None
    search: Optional[str] = None
    replace: Optional[str] = None


class ImprovementProposal(BaseModel):
    diagnosis: str
    changes: list[FileChange] = Field(max_length=3)
    expected_effect: str
