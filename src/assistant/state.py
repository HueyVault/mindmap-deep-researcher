import operator
from dataclasses import dataclass, field
from typing_extensions import TypedDict, Annotated

@dataclass(kw_only=True)
class SummaryState:
    research_topic: str = field(default=None)
    web_research_results: Annotated[list, operator.add] = field(default_factory=list)
    running_summary: str = field(default=None)
    needs_external_info: bool = field(default=False)
    search_query: str = field(default=None)
    research_loop_count: int = field(default=0)  # 추가

@dataclass(kw_only=True)
class SummaryStateInput:
    research_topic: str = field(default=None) # Report topic     

@dataclass(kw_only=True)
class SummaryStateOutput:
    running_summary: str = field(default=None) # Final report