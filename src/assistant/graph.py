import json
from enum import Enum 
from typing_extensions import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
# from langchain_ollama import ChatOllama
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import START, END, StateGraph

from assistant.configuration import Configuration, SearchAPI

# Core utilities
from assistant.utils import (
    deduplicate_and_format_sources,
    tavily_search,
    format_sources,
    perplexity_search,
    save_research_process,
    clear_session_files
)

# State management
from assistant.state import (
    SummaryState,
    SummaryStateInput,
    SummaryStateOutput
)

# Prompts
from assistant.prompts import (
    query_writer_instructions,
    summarizer_instructions,
    reflection_instructions,
    review_instructions,
    reasoner_instructions
)

def generate_query(state: SummaryState, config: RunnableConfig):
    """ Generate a query for web search """
    # 새로운 연구 시작 시 세션 파일 초기화
    clear_session_files()

    query_writer_instructions_formatted = (
        query_writer_instructions.format(research_topic=state.research_topic) +
        "\nYou must respond in the following JSON format only: {\"query\": \"your generated query here\"}"
    )

    configurable = Configuration.from_runnable_config(config)
    llm_json_mode = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp",
        temperature=0,
        convert_system_message_to_human=True
    )
    
    try:
        result = llm_json_mode.invoke(
            [SystemMessage(content=query_writer_instructions_formatted),
            HumanMessage(content=f"Generate a query for web search:")]
        )   
        
        try:
            query = json.loads(result.content)
            query_result = {"search_query": query.get('query', result.content.strip())}
        except json.JSONDecodeError:
            query_result = {"search_query": result.content.strip()}
            
        # 프롬프트와 결과 저장
        save_research_process(
            state,
            "Query Generation Step",
            f"Instructions:\n{query_writer_instructions_formatted}\n\n"
            f"LLM Response:\n{result.content}\n\n"
            f"Final Query:\n{query_result['search_query']}"
        )
        return query_result
    except Exception as e:
        print(f"Error in generate_query: {e}")
        return {"search_query": state.research_topic}

def web_research(state: SummaryState, config: RunnableConfig):
    """ Gather information from the web """
    
    configurable = Configuration.from_runnable_config(config)
    search_api = configurable.search_api.value if isinstance(configurable.search_api, Enum) else configurable.search_api

    # 쿼리 길이 제한 (400자)
    search_query = state.search_query[:400] if state.search_query else state.research_topic[:400]

    # 검색 시작 기록
    save_research_process(
        state,
        "Web Research Step",
        f"Search API: {search_api}\n"
        f"Search Query: {search_query}\n"
    )

    if search_api == "tavily":
        search_results = tavily_search(search_query, include_raw_content=True, max_results=1)
    elif search_api == "perplexity":
        search_results = perplexity_search(search_query, state.research_loop_count)
    else:
        raise ValueError(f"Unsupported search API: {configurable.search_api}")
    
    # 검색 결과 저장
    save_research_process(
        state,
        "Search Results",
        f"Raw Results:\n{search_results}\n\n"
        f"Formatted Sources:\n{format_sources(search_results)}"
    )
    
    # 기존 결과에 새로운 결과 추가 (중복 제거)
    current_results = state.web_research_results.copy()
    if isinstance(search_results, dict) and 'results' in search_results:
        # URL 기반 중복 제거
        seen_urls = {result['url'] for result in [item for sublist in current_results for item in sublist.get('results', [])] if isinstance(result, dict)}
        new_results = [
            result for result in search_results['results']
            if result['url'] not in seen_urls
        ]
        if new_results:
            current_results.append({'results': new_results})
    
    return SummaryState(
        research_topic=state.research_topic,
        web_research_results=current_results,  # 중복이 제거된 결과 사용
        running_summary=state.running_summary,
        needs_external_info=True,
        research_loop_count=state.research_loop_count,
        search_query=state.search_query
    )


from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential
)
import time
from functools import wraps

def rate_limit(delay=1):
    """Simple rate limiting decorator"""
    def decorator(func):
        last_called = [0]  # 마지막 호출 시간
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            elapsed = time.time() - last_called[0]
            if elapsed < delay:
                time.sleep(delay - elapsed)
            result = func(*args, **kwargs)
            last_called[0] = time.time()
            return result
        return wrapper
    return decorator

@rate_limit(delay=2)
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
def summarize_sources(state: SummaryState, config: RunnableConfig):
    """ Summarize the gathered sources """
    
    existing_summary = state.running_summary
    most_recent_web_research = state.web_research_results[-1]

    if existing_summary:
        human_message_content = (
            f"<User Input> \n {state.research_topic} \n <User Input>\n\n"
            f"<Existing Summary> \n {existing_summary} \n <Existing Summary>\n\n"
            f"<New Search Results> \n {most_recent_web_research} \n <New Search Results>"
        )
    else:
        human_message_content = (
            f"<User Input> \n {state.research_topic} \n <User Input>\n\n"
            f"<Search Results> \n {most_recent_web_research} \n <Search Results>"
        )

    configurable = Configuration.from_runnable_config(config)
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp",
        temperature=0,
        convert_system_message_to_human=True,
        max_retries=3,  # 재시도 횟수
        retry_delay=2,  # 재시도 간 대기 시간(초)
    )
    
    # 요약 시작 기록
    save_research_process(
        state,
        "Summarization Step",
        f"Instructions:\n{summarizer_instructions}\n\n"
        f"Input Content:\n{human_message_content}"
    )
    
    result = llm.invoke(
        [SystemMessage(content=summarizer_instructions),
        HumanMessage(content=human_message_content)]
    )

    running_summary = result.content

    while "<think>" in running_summary and "</think>" in running_summary:
        start = running_summary.find("<think>")
        end = running_summary.find("</think>") + len("</think>")
        running_summary = running_summary[:start] + running_summary[end:]

    # 요약 결과 저장
    save_research_process(
        state,
        "Summary Result",
        f"Generated Summary:\n{running_summary}"
    )

    return {"running_summary": running_summary}

def reflect_on_summary(state: SummaryState, config: RunnableConfig):
    """ Reflect on the summary and generate a follow-up query """

    reflection_instructions_formatted = (
        reflection_instructions.format(research_topic=state.research_topic) +
        "\nYou must respond in the following JSON format only: {\"follow_up_query\": \"your generated query here\"}"
    )

    configurable = Configuration.from_runnable_config(config)
    llm_json_mode = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp",
        temperature=0,
        convert_system_message_to_human=True
    )
    
    # 리플렉션 시작 기록
    save_research_process(
        state,
        "Reflection Step",
        f"Instructions:\n{reflection_instructions_formatted}\n\n"
        f"Current Summary:\n{state.running_summary}"
    )
    
    try:
        result = llm_json_mode.invoke(
            [SystemMessage(content=reflection_instructions_formatted),
            HumanMessage(content=f"Identify a knowledge gap and generate a follow-up web search query based on our existing knowledge: {state.running_summary}")]
        )   
        
        try:
            follow_up_query = json.loads(result.content)
            query = follow_up_query.get('follow_up_query')
        except json.JSONDecodeError:
            query = result.content.strip()
            
        if not query:
            query = f"Tell me more about {state.research_topic}"
            
        # 리플렉션 결과 저장
        save_research_process(
            state,
            "Reflection Result",
            f"LLM Response:\n{result.content}\n\n"
            f"Next Query:\n{query}"
        )
            
        return {"search_query": query}
    except Exception as e:
        print(f"Error in reflect_on_summary: {e}")
        return {"search_query": f"Tell me more about {state.research_topic}"}

def finalize_summary(state: SummaryState):
    """최종 연구 보고서 생성"""
    
    # sources_gathered는 이미 형식화된 문자열 리스트이므로 join으로 합치기
    sources_text = "\n".join(state.sources_gathered) if state.sources_gathered else "참고 문헌 없음"
    
    # 모든 분석 결과를 통합하여 최종 보고서 작성
    final_report = f"""# {state.research_topic} - 심층 연구 보고서

{state.running_summary}

## 연구 방법론
- 다양한 출처의 자료 수집 및 분석
- 총 {state.research_loop_count}회의 반복적 심층 분석 수행
- 교차 검증을 통한 신뢰성 확보

## 참고 문헌
{sources_text}
"""
    
    # 최종 보고서 저장
    save_research_process(
        state,
        "Final Research Report",
        f"연구 주제: {state.research_topic}\n\n"
        f"분석 반복 횟수: {state.research_loop_count}\n\n"
        f"최종 보고서:\n{final_report}"
    )
    
    return {"running_summary": final_report}


def review_summary(state: SummaryState, config: RunnableConfig):
    """Review and refine the current summary for relevance and coherence"""
    review_instructions_formatted = review_instructions.format(
        research_topic=state.research_topic
    )

    configurable = Configuration.from_runnable_config(config)
    llm_json_mode = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp",
        temperature=0,
        convert_system_message_to_human=True
    )
    
    try:
        result = llm_json_mode.invoke(
            [SystemMessage(content=review_instructions_formatted),
             HumanMessage(content=f"현재 요약:\n{state.running_summary}")]
        )
        
        try:
            # 코드 블록 마커 제거
            content = result.content
            if content.startswith("```json"):
                content = content[7:]  # "```json" 제거
            if content.endswith("```"):
                content = content[:-3]  # "```" 제거
            content = content.strip()
            
            review_result = json.loads(content)
            
            # 리뷰 결과 저장
            save_research_process(
                state,
                "Summary Review",
                f"평가 결과:\n"
                f"- 주제 관련성: {review_result['evaluation']['relevance']}/10\n"
                f"- 논리적 구조: {review_result['evaluation']['coherence']}/10\n"
                f"- 개선 필요 사항: {', '.join(review_result['evaluation']['issues'])}\n\n"
                f"제거된 내용:\n"
                f"{json.dumps(review_result['removed_content'], indent=2, ensure_ascii=False)}\n\n"
                f"개선된 요약:\n{review_result['refined_summary']}"
            )
            
            return {"running_summary": review_result['refined_summary']}
            
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON response: {result.content}")
            print(f"JSON error details: {str(e)}")
            return {"running_summary": state.running_summary}
            
    except Exception as e:
        print(f"Error in review_summary: {e}")
        return {"running_summary": state.running_summary}

def format_reasoning_chain(research_results):
    """추론 체인을 문서화된 형태로 변환"""
    formatted_chain = []
    
    for i, result in enumerate(research_results, 1):
        try:
            # JSON 형태로 저장된 추론 결과 파싱 시도
            if isinstance(result, str):
                # 추론 결과가 문자열 형태로 저장된 경우
                formatted_chain.append(f"### 분석 {i}\n{result}\n")
            else:
                # 이미 파싱된 결과나 다른 형태의 데이터인 경우
                formatted_chain.append(f"### 분석 {i}\n{result}\n")
        except Exception as e:
            # 파싱 실패 시 원본 텍스트 사용
            formatted_chain.append(f"### 분석 {i}\n{str(result)}\n")
    
    return "\n".join(formatted_chain)


def bullet_points(items):
    """리스트 항목을 글머리 기호 형식으로 변환"""
    return "\n".join(f"- {item}" for item in items)
    
def route_research(state: SummaryState) -> Literal["web_research", "review_summary", "finalize_summary"]:
    """웹 검색 진행 중 다음 단계를 결정
    
    research_loop_count:
    1-2회: 계속 검색
    3회: 중간 리뷰
    4-5회: 계속 검색
    6회 이상: 종료
    """
    if state.research_loop_count >= 6:  # 6회 이상이면 종료
        return "finalize_summary"
    # elif state.research_loop_count == 3:  # 3회차에 리뷰
    #     return "review_summary"
    return "web_research"

def route_after_review(state: SummaryState) -> Literal["web_research", "finalize_summary"]:
    """리뷰 후 다음 단계를 결정
    
    research_loop_count:
    3회 리뷰 후: 다시 검색으로
    6회 이상: 종료
    """
    if state.research_loop_count >= 6:  # 6회 이상이면 종료
        return "finalize_summary"
    return "web_research"  # 그 외에는 다시 검색
    
def format_analysis(analysis):
    """분석 결과를 포맷팅"""
    findings = analysis["current_analysis"].get("findings", [])
    if isinstance(findings, list):
        formatted_findings = "\n".join(f"- {finding}" for finding in findings)
    else:
        formatted_findings = str(findings)
    
    return f"""## 분석 결과
{formatted_findings}

## 추론 단계
{analysis.get('reasoning_step', '추론 단계 정보 없음')}
"""

def reason_from_sources(state: SummaryState, config: RunnableConfig):
    """주제에 대한 심층 분석 및 추론 수행"""
    # 상태 확인을 위한 로깅 추가
    print(f"Reasoning about topic: {state.research_topic}")
    print(f"Current state: {state}")
    
    existing_analysis = state.running_summary
    current_context = state.web_research_results[-1] if state.web_research_results else ""

# 추론 지시사항 수정
    reasoning_prompt = """당신은 주어진 주제에 대해 심층적인 분석과 추론을 수행하는 전문 연구원입니다.

현재 진행 상황: {current_iteration}/5회차

[중요 제한사항]
- 웹 검색은 전체 연구 과정에서 최대 5회만 허용됩니다
- 각 검색은 신중하게 선택되어야 하며, 연구의 핵심 질문을 해결하는데 집중해야 합니다
- 불필요한 검색은 제한된 기회를 낭비하게 됩니다
- 마지막 회차에서는 반드시 결론을 도출해야 합니다

추론 시 다음을 고려해주세요:
1. 현재까지 수집된 정보의 충분성
2. 남은 검색 기회를 고려한 정보 수집 우선순위
3. 정보가 충분하다면 최종 결론 도출

정보 수집을 위한 방법:
1. <SEARCH>검색어</SEARCH> : 웹 검색 요청 (최대 400자)
   - 남은 검색 횟수를 고려하여 신중하게 사용
   - 새로운 정보가 반드시 필요한 경우에만 사용

※ 효율적인 연구 전략:
1. 첫 1-2회 검색으로 핵심 정보 수집
2. 중간 단계에서는 마인드맵 활용하여 기존 정보 분석
3. 마지막 검색들은 핵심 격차를 메우는데 사용
4. 5회차에서는 반드시 최종 결론 도출

중요: 반드시 아래 JSON 형식으로만 응답해주세요. 다른 텍스트나 설명을 추가하지 마세요."""

    human_message = (
        f"<연구 주제>\n{state.research_topic}\n</연구 주제>\n\n"
        f"<현재 맥락>\n{current_context}\n</현재 맥락>\n\n"
        f"<기존 분석>\n{existing_analysis or '아직 분석이 없습니다.'}\n</기존 분석>\n\n"
        f"<남은 검색 횟수>\n{5 - (state.research_loop_count or 0)}회\n</남은 검색 횟수>\n\n"
        f"""응답 형식:
{{
    "reasoning_step": "현재 추론 단계 설명",
    "current_analysis": {{
        "findings": ["현재까지의 발견사항"],
        "gaps": ["확인된 정보 격차"],
        "completeness": "현재까지의 분석 완성도 평가"
    }},
    "next_action": {{
        "type": "continue | search | mindmap",
        "query": "검색어 또는 질의 (action이 continue가 아닌 경우, 검색어는 400자 이내)",
        "rationale": "이 행동을 선택한 이유 (남은 검색 횟수 고려)"
    }}
}}"""
    )

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp",
        temperature=0,
        convert_system_message_to_human=True
    )
    
    try:
        result = llm.invoke(
            [SystemMessage(content=reasoning_prompt.format(current_iteration=state.research_loop_count + 1)),
             HumanMessage(content=human_message)]
        )
        
        # LLM 응답 로깅 (JSON 파싱 전)
        save_research_process(
            state,
            "Raw LLM Response",
            f"System Prompt:\n{reasoning_prompt.format(current_iteration=state.research_loop_count + 1)}\n\n"
            f"Human Message:\n{human_message}\n\n"
            f"Raw Response:\n{result.content}"
        )
        
        try:
            # 마크다운 코드 블록 제거
            content = result.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
                
            analysis = json.loads(content)
            
            # 파싱된 JSON 로깅 추가
            save_research_process(
                state,
                "Parsed JSON",
                f"Cleaned content:\n{content}\n\n"
                f"Parsed analysis:\n{json.dumps(analysis, indent=2, ensure_ascii=False)}"
            )
            
        except json.JSONDecodeError as e:
            print(f"JSON 파싱 에러: {e}")
            print(f"LLM 응답: {result.content}")
            print(f"정제된 콘텐츠: {content}")
            raise
        
        # 추론 과정 저장
        save_research_process(
            state,
            "Reasoning Step",
            f"Current Context:\n{current_context}\n\n"
            f"Analysis:\n{json.dumps(analysis, indent=2, ensure_ascii=False)}"
        )
        
        # 다음 액션에 따라 상태 업데이트
        if analysis["next_action"]["type"] == "search":
            return SummaryState(
                research_topic=state.research_topic,
                search_query=analysis["next_action"]["query"],
                running_summary=analysis["current_analysis"].get("findings", []),
                needs_external_info=True,
                research_loop_count=(state.research_loop_count or 0) + 1,
                web_research_results=state.web_research_results
            )
        elif analysis["next_action"]["type"] == "continue":
            final_analysis = format_analysis(analysis)
            
            # 최종 분석 저장
            save_research_process(
                state,
                "Final Analysis",
                f"Reasoning Complete\n\n{final_analysis}"
            )
            
            return SummaryState(
                research_topic=state.research_topic,
                running_summary=final_analysis,
                needs_external_info=False,
                research_loop_count=(state.research_loop_count or 0) + 1,
                web_research_results=state.web_research_results,
                search_query=state.search_query
            )
            
    except Exception as e:
        print(f"Error in reasoning: {e}")
        save_research_process(
            state,
            "Reasoning Error",
            f"Error occurred: {str(e)}"
        )
        return SummaryState(
            research_topic=state.research_topic,
            running_summary=state.running_summary,
            needs_external_info=False,
            research_loop_count=(state.research_loop_count or 0) + 1,
            web_research_results=state.web_research_results,
            search_query=state.search_query
        )

def format_final_report(state: SummaryState) -> SummaryState:
    """최종 연구 보고서 형식화"""
    
    # running_summary 타입 체크 및 변환
    current_summary = state.running_summary
    if isinstance(current_summary, list):
        current_summary = "\n".join(f"- {item}" for item in current_summary)
    elif current_summary is None:
        current_summary = "분석 결과 없음"
    
    # LLM 설정
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp",
        temperature=0,
        convert_system_message_to_human=True
    )
    
    # 기존 분석 결과를 LLM에게 전달
    final_analysis = llm.invoke([
        SystemMessage(content="""주어진 연구 결과를 바탕으로 학술적인 최종 보고서를 작성해주세요.
다음 구조를 반드시 포함해야 합니다:
1. 연구 개요
2. 연구 방법
3. 주요 발견사항
4. 상세 분석
5. 결론 및 시사점"""),
        HumanMessage(content=f"""
연구 주제: {state.research_topic}
분석 결과: {current_summary}
분석 횟수: {state.research_loop_count}회
""")
    ])

   
    # 최종 보고서 구성
    final_report = f"""# {state.research_topic} - 최종 연구 보고서

{final_analysis.content}

## 참고 문헌
{format_sources(state.web_research_results)}
"""
    
    # 최종 보고서 저장
    save_research_process(
        state,
        "Final Research Report",
        final_report
    )
    
    return SummaryState(
        research_topic=state.research_topic,
        running_summary=final_report,
        needs_external_info=False,
        research_loop_count=state.research_loop_count,
        web_research_results=state.web_research_results,
        search_query=state.search_query
    )




# Add nodes and edges 
builder = StateGraph(SummaryState, input=SummaryStateInput, output=SummaryStateOutput)

def initialize_research(state_input: SummaryStateInput) -> SummaryState:
    """사용자 입력으로 초기 상태 생성"""
    clear_session_files()

    return SummaryState(
        research_topic=state_input.research_topic,
        web_research_results=[],
        running_summary=None,
        needs_external_info=True,  # 처음에는 항상 정보 수집이 필요
        search_query=state_input.research_topic  # 초기 검색어는 연구 주제로 설정
    )

# 나머지 노드와 엣지 구성은 동일
builder.add_node("initialize", initialize_research)
builder.add_node("reason_from_sources", reason_from_sources)
builder.add_node("web_research", web_research)
builder.add_node("generate_final_report", format_final_report)

# Add edges
builder.add_edge(START, "initialize")
builder.add_edge("initialize", "reason_from_sources")
builder.add_edge("web_research", "reason_from_sources")

builder.add_conditional_edges(
    "reason_from_sources",
    lambda x: "web_research" if x.needs_external_info else "generate_final_report",
    {
        "web_research": "web_research",
        "generate_final_report": "generate_final_report"
    }
)

builder.add_edge("generate_final_report", END)

graph = builder.compile()


