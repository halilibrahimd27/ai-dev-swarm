"""CrewAI-backed crews and their Protocols.

Concrete impls import CrewAI lazily inside their constructors so that
unit tests substituting fake crews never pay the CrewAI import cost.
"""

from aidevswarm.crews.build.crew import CrewaiBuildCrew
from aidevswarm.crews.ideation.crew import CrewaiIdeationCrew
from aidevswarm.crews.planning.crew import CrewaiPlanningCrew
from aidevswarm.crews.protocols import BuildCrew, IdeationCrew, PlanningCrew

__all__ = [
    "BuildCrew",
    "CrewaiBuildCrew",
    "CrewaiIdeationCrew",
    "CrewaiPlanningCrew",
    "IdeationCrew",
    "PlanningCrew",
]
