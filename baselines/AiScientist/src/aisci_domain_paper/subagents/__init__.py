from aisci_domain_paper.subagents.env_setup import EnvSetupSubagent
from aisci_domain_paper.subagents.experiment import PaperExperimentSubagent
from aisci_domain_paper.subagents.generic import ExploreSubagent, GeneralSubagent, PlanSubagent
from aisci_domain_paper.subagents.implementation import PaperImplementationSubagent
from aisci_domain_paper.subagents.prioritization import PaperPrioritizationSubagent
from aisci_domain_paper.subagents.resource_download import ResourceDownloadSubagent


def subagent_class_for_kind(kind: str):
    normalized = kind.strip().lower()
    mapping = {
        "prioritization": PaperPrioritizationSubagent,
        "implementation": PaperImplementationSubagent,
        "experiment": PaperExperimentSubagent,
        "explore": ExploreSubagent,
        "plan": PlanSubagent,
        "general": GeneralSubagent,
        "generic": GeneralSubagent,
        "env_setup": EnvSetupSubagent,
        "resource_download": ResourceDownloadSubagent,
    }
    return mapping[normalized]


__all__ = [
    "EnvSetupSubagent",
    "ExploreSubagent",
    "GeneralSubagent",
    "PaperExperimentSubagent",
    "PaperImplementationSubagent",
    "PaperPrioritizationSubagent",
    "PlanSubagent",
    "ResourceDownloadSubagent",
    "subagent_class_for_kind",
]
