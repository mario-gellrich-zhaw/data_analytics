"""One agent per process phase: prompt file, model role, tool whitelist, output schema."""
from __future__ import annotations

from agents.base import Agent
from ada.schemas import (CollectionReport, DataRequirements, EDAOutput, EvaluationOutput, ModelingOutput,
                         ObjectiveSpec, PrepOutput, PresenterOutput)

READ_TOOLS = ["list_files", "read_file", "preview_table"]


class ObjectiveAgent(Agent):
    name = "ObjectiveAgent"
    role = "objective"
    prompt = "objective"
    tools: list[str] = []
    output_model = ObjectiveSpec


class DataRequirementsAgent(Agent):
    name = "DataRequirementsAgent"
    role = "data_requirements"
    prompt = "data_requirements"
    tools = ["web_search", "list_local_datasets"]
    output_model = DataRequirements


class DataCollectorAgent(Agent):
    name = "DataCollectorAgent"
    role = "data_collector"
    prompt = "data_collector"
    tools = ["web_search", "fetch_url", "download_file", "list_local_datasets", "import_local_dataset",
             "geocode_addresses", "osm_poi_counts", "list_files", "preview_table", "read_file"]
    output_model = CollectionReport


class DataEngineerAgent(Agent):
    name = "DataEngineerAgent"
    role = "data_engineer"
    prompt = "data_engineer"
    tools = READ_TOOLS + ["run_python", "write_file"]
    output_model = PrepOutput


class EDAAgent(Agent):
    name = "EDAAgent"
    role = "eda"
    prompt = "eda"
    tools = READ_TOOLS + ["run_python", "write_file"]
    output_model = EDAOutput


class ModelingAgent(Agent):
    name = "ModelingAgent"
    role = "modeling"
    prompt = "modeling"
    tools = READ_TOOLS + ["run_python"]
    output_model = ModelingOutput


class EvaluationAgent(Agent):
    name = "EvaluationAgent"
    role = "evaluation"
    prompt = "evaluation"
    tools = READ_TOOLS + ["run_python", "write_file"]
    output_model = EvaluationOutput


class PresenterAgent(Agent):
    name = "PresenterAgent"
    role = "presenter"
    prompt = "presenter"
    tools = ["list_files", "read_file", "write_file"]
    output_model = PresenterOutput


PHASE_AGENTS: dict[str, type[Agent]] = {
    "business_objectives": ObjectiveAgent,
    "define_data": DataRequirementsAgent,
    "collect_data": DataCollectorAgent,
    "prepare_store": DataEngineerAgent,
    "eda": EDAAgent,
    "modeling": ModelingAgent,
    "evaluation": EvaluationAgent,
    "present_results": PresenterAgent,
}
