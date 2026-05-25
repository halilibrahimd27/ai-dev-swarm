"""State machine + tick + scheduler that drive the swarm."""

from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.consolidation import (
    build_consolidation_spec,
    should_insert_consolidation,
)
from aidevswarm.orchestrator.scheduler import IntervalJob, ProjectPool, Scheduler
from aidevswarm.orchestrator.state_machine import (
    IllegalTransition,
    assert_legal_milestone,
    assert_legal_project,
    legal_milestone_successors,
    legal_project_successors,
)
from aidevswarm.orchestrator.tick import Tick, TickDeps

__all__ = [
    "AutoSplitPredictor",
    "IllegalTransition",
    "IntervalJob",
    "ProjectPool",
    "Scheduler",
    "Tick",
    "TickDeps",
    "assert_legal_milestone",
    "assert_legal_project",
    "build_consolidation_spec",
    "legal_milestone_successors",
    "legal_project_successors",
    "should_insert_consolidation",
]
