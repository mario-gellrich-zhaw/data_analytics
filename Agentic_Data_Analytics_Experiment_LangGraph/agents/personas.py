"""Agent persona definitions for the Data Analytics Process Model demo.

Builds the `AgentConfig`s (see graph.py) the conversation moves through: a
Product Manager, a Data Analyst, and a Data Engineer, each with a no-tools
("discussion" / "review") and/or tools-bound ("action") variant depending
on which phase of the process model they're speaking in. The persona text
itself lives in prompts.py; this module decides which model and which
tools each variant gets.
"""

from typing import Callable, NamedTuple

from langchain_openai import ChatOpenAI

from agents import prompts
from agents.graph import AgentConfig
from tools.schemas import (
    DISCARD_DATASET_SCHEMA,
    DOWNLOAD_DATASET_SCHEMA,
    MAKE_SKETCH_SCHEMA,
    PREVIEW_DATA_SCHEMA,
    PROFILE_DATA_SCHEMA,
    RUN_PREP_CODE_SCHEMA,
    RUN_SCRAPER_SCHEMA,
    RUN_SQL_QUERY_SCHEMA,
    SEARCH_OPEN_DATA_SCHEMA,
    SHOW_TO_CLASS_SCHEMA,
    LOOK_UP_PAST_RUNS_SCHEMA,
    STORE_TO_DATABASE_SCHEMA,
    WRITE_PREP_CODE_SCHEMA,
    WRITE_SCRAPER_CODE_SCHEMA,
)

MODEL = "gpt-4o-mini"
# Writing and debugging a real scraper against real responses needs a
# stronger model: in live tests gpt-4o-mini kept guessing field names from
# memory instead of reading the real response structure, and gave up on
# working sources; gpt-4.1 read the structure and handled blocks properly.
# The same goes for Step 4's own cleaning/enrichment scripts.
CODE_WRITING_MODEL = "gpt-4.1"

# What the Step 4 cleaning / enrichment coder can use.
PREP_CODING_TOOLS = [
    PREVIEW_DATA_SCHEMA,
    PROFILE_DATA_SCHEMA,
    WRITE_PREP_CODE_SCHEMA,
    RUN_PREP_CODE_SCHEMA,
    MAKE_SKETCH_SCHEMA,
    SHOW_TO_CLASS_SCHEMA,
    LOOK_UP_PAST_RUNS_SCHEMA,
]
# What a reviewer next to a coder can use: point at a real example.
REVIEWER_TOOLS = [MAKE_SKETCH_SCHEMA, SHOW_TO_CLASS_SCHEMA]


def _model(tools: list[dict] | None = None, model: str | None = None):
    base = ChatOpenAI(model=model or MODEL)
    return base.bind_tools(tools) if tools else base


class DemoAgents(NamedTuple):
    """The agent variants a run's phases pick from — see
    app/demo_run.py's step-by-step calls into `run_phase()` for which
    variant plays which phase."""

    product_manager: AgentConfig
    data_analyst_notools: AgentConfig
    data_analyst_with_tools: AgentConfig
    data_engineer_collecting: AgentConfig
    data_engineer_notools: AgentConfig
    data_engineer_cleaning: AgentConfig
    data_analyst_reviewing_cleaning: AgentConfig
    data_analyst_enriching: AgentConfig
    data_engineer_reviewing_enrichment: AgentConfig
    data_engineer_storing: AgentConfig


def build_agents(
    analyst_working_notes: Callable[[], str] | None = None,
    prep_working_notes: Callable[[], str] | None = None,
) -> DemoAgents:
    """Construct a fresh set of agents for one run. Each agent is a
    LangChain `ChatOpenAI`, `.bind_tools(...)`-ed when tools apply for the
    phase it plays — a model can only ever request a tool actually bound
    to it for that call. `analyst_working_notes` is the tool-using Data
    Analyst's private memory of its last scraper run, `prep_working_notes`
    the Step 4 coder's memory of its last cleaning/enrichment run (see
    RunTools)."""
    return DemoAgents(
        product_manager=AgentConfig(
            name="Product Manager",
            persona=prompts.PRODUCT_MANAGER_PERSONA,
            model=_model([MAKE_SKETCH_SCHEMA]),
        ),
        data_analyst_notools=AgentConfig(
            name="Data Analyst",
            persona=prompts.DATA_ANALYST_NOTOOLS_PERSONA,
            model=_model([MAKE_SKETCH_SCHEMA]),
        ),
        data_analyst_with_tools=AgentConfig(
            name="Data Analyst",
            persona=prompts.DATA_ANALYST_WITH_TOOLS_PERSONA,
            model=_model(
                [
                    WRITE_SCRAPER_CODE_SCHEMA,
                    RUN_SCRAPER_SCHEMA,
                    SEARCH_OPEN_DATA_SCHEMA,
                    DOWNLOAD_DATASET_SCHEMA,
                    PREVIEW_DATA_SCHEMA,
                    DISCARD_DATASET_SCHEMA,
                    MAKE_SKETCH_SCHEMA,
                    SHOW_TO_CLASS_SCHEMA,
                    LOOK_UP_PAST_RUNS_SCHEMA,
                ],
                model=CODE_WRITING_MODEL,
            ),
            working_notes=analyst_working_notes,
        ),
        data_engineer_collecting=AgentConfig(
            name="Data Engineer",
            persona=prompts.DATA_ENGINEER_COLLECTING_PERSONA,
            model=_model(REVIEWER_TOOLS),
        ),
        data_engineer_notools=AgentConfig(
            name="Data Engineer",
            persona=prompts.DATA_ENGINEER_NOTOOLS_PERSONA,
            model=_model([MAKE_SKETCH_SCHEMA]),
        ),
        data_engineer_cleaning=AgentConfig(
            name="Data Engineer",
            persona=prompts.DATA_ENGINEER_CLEANING_PERSONA,
            model=_model(PREP_CODING_TOOLS, model=CODE_WRITING_MODEL),
            working_notes=prep_working_notes,
        ),
        data_analyst_reviewing_cleaning=AgentConfig(
            name="Data Analyst",
            persona=prompts.DATA_ANALYST_REVIEWING_CLEANING_PERSONA,
            model=_model(REVIEWER_TOOLS),
        ),
        data_analyst_enriching=AgentConfig(
            name="Data Analyst",
            persona=prompts.DATA_ANALYST_ENRICHING_PERSONA,
            model=_model(PREP_CODING_TOOLS, model=CODE_WRITING_MODEL),
            working_notes=prep_working_notes,
        ),
        data_engineer_reviewing_enrichment=AgentConfig(
            name="Data Engineer",
            persona=prompts.DATA_ENGINEER_REVIEWING_ENRICHMENT_PERSONA,
            model=_model(REVIEWER_TOOLS),
        ),
        data_engineer_storing=AgentConfig(
            name="Data Engineer",
            persona=prompts.DATA_ENGINEER_STORING_PERSONA,
            model=_model(
                [
                    PREVIEW_DATA_SCHEMA,
                    PROFILE_DATA_SCHEMA,
                    STORE_TO_DATABASE_SCHEMA,
                    RUN_SQL_QUERY_SCHEMA,
                    MAKE_SKETCH_SCHEMA,
                    SHOW_TO_CLASS_SCHEMA,
                ]
            ),
        ),
    )
