# graph.py
from enum import Enum
from typing_extensions import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
# from langchain_ollama import ChatOllama
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import START, END, StateGraph

from assistant.configuration import Configuration, SearchAPI
from assistant.mind_map import MindMapAgent

import os
import re
import time
import json

# Core utilities
from assistant.utils import (
    deduplicate_and_format_sources,
    tavily_search,
    format_sources,
    perplexity_search,
    save_research_process,
    clear_session_files,
    global_request_limiter,
    update_node_status
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

def web_research(state: SummaryState, config: RunnableConfig):
    """ Gather information from the web """
    
    # 노드 시작 상태 업데이트
    update_node_status("web_research", "시작", f"검색 쿼리: {state.search_query}")
    
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
    
    new_state = SummaryState(
        research_topic=state.research_topic,
        web_research_results=current_results,  # 중복이 제거된 결과 사용
        running_summary=state.running_summary,
        needs_external_info=True,
        research_loop_count=state.research_loop_count,
        search_query=state.search_query
    )

    # 결과 반환 전 노드 완료 상태 업데이트
    update_node_status("web_research", "완료", f"검색 결과: {current_results}")
    return new_state


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

def reason_from_sources(state: SummaryState, config: RunnableConfig):
    """소스로부터 추론 및 다음 단계 결정 - 반성(Reflection)과 계획(Planning) 포함"""
    try:
        # 노드 시작 상태 업데이트
        update_node_status("reason_from_sources", "시작", f"반복 {state.research_loop_count}회: 분석 중")
        
        # Configuration에서 Neo4j 설정 가져오기
        configurable = Configuration.from_runnable_config(config)
        max_loops = configurable.max_web_research_loops
        
        # Mind Map 에이전트 초기화
        mind_map = MindMapAgent(
            url=configurable.neo4j_uri,
            username=configurable.neo4j_username,
            password=configurable.neo4j_password
        )
        
        # 현재 상태를 Mind Map에 자동 기록
        mind_map.auto_update(state, "reasoning_preparation")
        
        # 웹 연구 결과 포맷
        formatted_sources = deduplicate_and_format_sources(
            state.web_research_results, 
            max_tokens_per_source=5000
        )
        
        # 이전 추론 결과가 있으면 포함
        current_summary = ""
        if state.running_summary:
            if isinstance(state.running_summary, list):
                current_summary = "\n".join(state.running_summary) 
            else:
                current_summary = state.running_summary
        
        # Mind Map에서 관련 컨텍스트 검색 - 이전 반성과 계획도 포함
        mind_map_context = ""
        reflection_context = ""
        planning_context = ""
        
        if current_summary:
            # 일반적인 컨텍스트 검색
            context_query = f"이 연구에서 이미 발견한 중요한 사실은 무엇인가요? 주제: {state.research_topic}"
            mind_map_context = mind_map.retrieve_context(context_query, state.research_topic)
            
            # 이전 반성 검색
            reflection_query = f"이 연구에서 진행된 이전 반성(reflection)들은 무엇인가요? 주제: {state.research_topic}"
            reflection_context = mind_map.retrieve_context(reflection_query, state.research_topic)
            
            # 이전 계획 검색
            planning_query = f"이 연구에서 수립된 이전 계획(planning)들은 무엇인가요? 주제: {state.research_topic}"
            planning_context = mind_map.retrieve_context(planning_query, state.research_topic)
        
        # 추론용 프롬프트 - 반성과 계획 요소 추가
        modified_reasoner_instructions = """당신은 높은 수준의 학술 연구를 수행하는 전문 연구원입니다. 
당신의 목표는 주어진 주제에 대해 깊이 있고 체계적인 분석을 수행하는 것입니다.

현재 진행 상황: {current_iteration}/{max_loops}회차

<연구 시스템>
당신은 다음 세 단계의 프로세스를 통해 연구를 수행해야 합니다:

1. 계획(PLANNING)
   - 현재까지의 연구 상태 평가
   - 핵심 질문과 연구 방향 설정
   - 필요한 정보와 그 우선순위 결정
   - 앞으로의 단계별 연구 접근 방식 수립

2. 실행(EXECUTION)
   - 정보 통합: 여러 출처 비교/대조, 패턴 파악
   - 심층 분석: 근본 원인과 영향 평가
   - 비판적 사고: 주장 검증, 한계점 식별

3. 반성(REFLECTION)
   - 현재 연구의 깊이와 범위 평가
   - 핵심 가설의 검증 상태 확인
   - 미해결 질문과 부족한 증거 식별
   - 연구의 약점과 보완점 명확화

<이전 계획 내용>
{planning_context}

<이전 반성 내용>
{reflection_context}

정보 수집을 위한 방법:
1. <SEARCH>검색어</SEARCH> : 웹 검색 요청 (최대 400자)
   - 남은 검색 횟수를 고려하여 신중하게 사용
   - 계획 단계에서 결정된 중요 질문에 대해서만 사용

2. <MIND_MAP_QUERY>질의</MIND_MAP_QUERY> : 마인드맵 질의 요청
   - 이전 분석/계획/반성 내용을 참조할 때 사용
   - 검색 횟수를 소비하지 않음

<마인드맵 컨텍스트>
{mind_map_context}

<연구 주제>
{research_topic}

주제에 대한 철저한 분석을 텍스트 형식으로 제공하세요. 반드시 각 회차마다 PLANNING, EXECUTION, REFLECTION 섹션을 모두 포함해야 합니다.

- 처음에는 계획을 세우고
- 그 계획에 따라 분석을 실행하고
- 마지막에 반성을 통해 다음 단계를 준비하세요

필요시 <SEARCH> 태그나 <MIND_MAP_QUERY> 태그를 사용하여 추가 정보를 요청할 수 있습니다. 
JSON 형식이 아닌 일반 텍스트로 응답해주세요.
"""
        
        # LLM 설정
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash-exp",
            temperature=0.2,
            convert_system_message_to_human=True
        )
        
        save_research_process(
            state,
            "Reasoning Step",
            f"Research Topic: {state.research_topic}\n"
            f"Current Summary: {current_summary}\n"
            f"Search Query: {state.search_query}\n"
            f"Mind Map Context: {mind_map_context}\n"
            f"Reflection Context: {reflection_context}\n"
            f"Planning Context: {planning_context}"
        )
        
        time.sleep(3)
        
        # 수정된 LLM 호출 코드:
        result = global_request_limiter.get_llm_response(
            llm=llm,
            messages=[
                SystemMessage(content=modified_reasoner_instructions.format(
                    research_topic=state.research_topic,
                    mind_map_context=mind_map_context if mind_map_context else "마인드맵에 아직 충분한 정보가 없습니다.",
                    reflection_context=reflection_context if reflection_context else "아직 반성 내용이 없습니다.",
                    planning_context=planning_context if planning_context else "아직 계획 내용이 없습니다.",
                    current_iteration=state.research_loop_count + 1,
                    max_loops=max_loops
                )),
                HumanMessage(content=f"""
                <연구 주제>
                {state.research_topic}
                </연구 주제>
                
                <현재 맥락>
                {formatted_sources}
                </현재 맥락>
                
                <기존 분석>
                {current_summary or '아직 분석이 없습니다.'}
                </기존 분석>
                
                <남은 검색 횟수>
                {max_loops - (state.research_loop_count or 0)}회
                </남은 검색 횟수>
                
                응답 지침:
                1. 먼저 PLANNING 섹션에서 연구 계획을 수립하세요.
                2. 다음으로 EXECUTION 섹션에서 분석을 수행하세요.
                3. 마지막으로 REFLECTION 섹션에서 현재 연구를 평가하세요.
                
                각 섹션을 명확히 구분하고, 필요할 때 <SEARCH>나 <MIND_MAP_QUERY> 태그를 사용하세요.
                """)
            ]
        )

        # 반성 및 계획 내용을 Mind Map에 저장
        try:

            # 계획 내용 추출 및 저장
            plan_match = re.search(r'(?i)# *PLANNING[\s\S]*?(?=# *EXECUTION|# *REFLECTION|$)', result.content)
            if plan_match:
                plan_content = plan_match.group(0).strip()
                mind_map.create_research_node(
                    research_topic=state.research_topic,
                    node_type="ResearchPlan",
                    content=plan_content,
                    iteration=state.research_loop_count + 1
                )
            
            # 반성 내용 추출 및 저장
            reflection_match = re.search(r'(?i)# *REFLECTION[\s\S]*?(?=# *|$)', result.content)
            if reflection_match:
                reflection_content = reflection_match.group(0).strip()
                mind_map.create_research_node(
                    research_topic=state.research_topic,
                    node_type="ResearchReflection",
                    content=reflection_content,
                    iteration=state.research_loop_count + 1
                )
        except Exception as e:
            print(f"Mind Map에 계획/반성 저장 중 오류: {e}")
            save_research_process(state, "Mind Map Update Error", str(e))
        
        # Mind Map 토큰 감지
        if "<MIND_MAP_QUERY>" in result.content and "</MIND_MAP_QUERY>" in result.content:

            query_text = re.search(r'<MIND_MAP_QUERY>(.*?)</MIND_MAP_QUERY>', result.content, re.DOTALL)
            if query_text:
                query = query_text.group(1).strip()
                
                # 마인드맵 쿼리 설정
                new_state = SummaryState(
                    research_topic=state.research_topic,
                    search_query=query,
                    running_summary=result.content,
                    needs_external_info=True,
                    research_loop_count=state.research_loop_count,
                    web_research_results=state.web_research_results,
                    query_type="mind_map"  # 여기서 설정!
                )
                
                # 상태 업데이트 후 반환
                update_node_status("reason_from_sources", "완료", f"다음 검색 쿼리: {query}")
                return new_state
                
        # 웹 검색 토큰 처리
        elif "<SEARCH>" in result.content and "</SEARCH>" in result.content:
            start = result.content.find("<SEARCH>") + len("<SEARCH>")
            end = result.content.find("</SEARCH>")
            search_query = result.content[start:end].strip()

            new_state = SummaryState(
                research_topic=state.research_topic,
                running_summary=result.content,
                needs_external_info=True,
                research_loop_count=state.research_loop_count + 1,
                web_research_results=state.web_research_results,
                search_query=search_query,
                query_type="web_search"
            )
            # 상태 업데이트 후 반환
            update_node_status("reason_from_sources", "완료", f"다음 검색 쿼리: {search_query}")
            return new_state   

        else:  # 더 이상 외부 정보가 필요 없는 경우
            new_state = SummaryState(
                research_topic=state.research_topic,
                running_summary=result.content,
                needs_external_info=False,
                research_loop_count=state.research_loop_count + 1,
                web_research_results=state.web_research_results,
                search_query=state.search_query,
                query_type="reason_from_sources"
            )
            # 상태 업데이트 후 반환
            update_node_status("reason_from_sources", "완료", f"분석 완료: {state.research_loop_count+1}회")
            return new_state   

    except Exception as e:
        print(f"추론 중 오류 발생: {e}")
        save_research_process(state, "Reasoning Error", str(e))

        new_state = SummaryState(
            research_topic=state.research_topic,
            running_summary=state.running_summary,
            needs_external_info=False,
            research_loop_count=state.research_loop_count + 1,
            web_research_results=state.web_research_results,
            search_query=state.search_query,
            query_type="reason_from_sources"
        )

        # 오류 상태 업데이트 후 반환
        update_node_status("reason_from_sources", "오류", f"오류 발생: {str(e)}")
        return new_state    
    

def query_mind_map(state: SummaryState, config: RunnableConfig) -> SummaryState:
    """마인드맵 쿼리 수행 함수"""
    # 노드 시작 상태 업데이트
    update_node_status("query_mind_map", "시작", f"마인드맵 쿼리: {state.search_query}")
    
    try:
        # Configuration에서 Neo4j 설정 가져오기
        configurable = Configuration.from_runnable_config(config)
        
        # Mind Map 에이전트 초기화
        mind_map = MindMapAgent(
            url=configurable.neo4j_uri,
            username=configurable.neo4j_username,
            password=configurable.neo4j_password
        )

        # 마인드맵 쿼리 수행
        query = state.search_query
        research_topic = state.research_topic
        
        # 로그 기록
        save_research_process(
            state,
            "Mind Map Query",
            f"마인드맵 쿼리: {query}"
        )
        
        time.sleep(1)

        # 마인드맵 쿼리 실행
        map_result = mind_map.retrieve_context(query, research_topic)
        
        # 쿼리 결과 로깅
        save_research_process(
            state,
            "Mind Map Result",
            f"쿼리: {query}\n결과: {map_result}"
        )
        
        # 결과를 응답에 통합
        modified_content = state.running_summary.replace(
            f"<MIND_MAP_QUERY>{query}</MIND_MAP_QUERY>",
            f"<MIND_MAP_RESULT>\n{map_result}\n</MIND_MAP_RESULT>"
        )
        
        # 수정된 내용으로 다시 추론
        new_state = SummaryState(
            research_topic=state.research_topic,
            search_query="",  # 쿼리 초기화
            running_summary=modified_content,
            needs_external_info=True,  # 다시 추론으로 돌아감
            research_loop_count=state.research_loop_count,  # 카운트 유지
            web_research_results=state.web_research_results,
            query_type="reason_from_sources" # ""  # 쿼리 타입 초기화
        )
        # 결과 반환 전 노드 완료 상태 업데이트
        update_node_status("query_mind_map", "완료", "마인드맵 쿼리 완료")
        return new_state
        
    except Exception as e:
        print(f"마인드맵 쿼리 중 오류 발생: {e}")
        save_research_process(state, "Mind Map Query Error", str(e))
        
        # 오류 발생 시 에러 메시지 추가
        error_content = state.running_summary.replace(
            f"<MIND_MAP_QUERY>{state.search_query}</MIND_MAP_QUERY>",
            f"<MIND_MAP_ERROR>마인드맵 쿼리 중 오류 발생: {str(e)}</MIND_MAP_ERROR>"
        )
        
        new_state = SummaryState(
            research_topic=state.research_topic,
            running_summary=error_content,
            needs_external_info=True,  # 다시 추론
            research_loop_count=state.research_loop_count,
            web_research_results=state.web_research_results,
            search_query=""
        )
        # 결과 반환 전 노드 완료 상태 업데이트
        update_node_status("query_mind_map", "완료", "마인드맵 쿼리 오류")
        return new_state

def format_final_report(state: SummaryState) -> SummaryState:
    """최종 연구 보고서 형식화"""
    # 노드 시작 상태 업데이트
    update_node_status("finalize_summary", "시작", "최종 보고서 작성 중")
    
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
        convert_system_message_to_human=True,
        max_retries=3,  # 재시도 횟수 설정
        timeout=30  # 타임아웃 설정
    )
    
    # 기존 분석 결과를 LLM에게 전달
    try:
        import time
        
        # 최종 보고서 생성 시도
        save_research_process(
            state,
            "Final Report Generation",
            "최종 보고서 생성 시작..."
        )
        
        # API 호출 전 잠시 대기 (API 요청 속도 제한)
        time.sleep(2)
        
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
        
    except Exception as e:
        save_research_process(
            state,
            "Final Report Generation Error",
            f"최종 보고서 생성 중 오류 발생: {str(e)}"
        )
        
        # 오류 발생 시 간단한 보고서로 대체
        final_report = f"""# {state.research_topic} - 최종 연구 보고서

## 연구 개요
{state.research_topic}에 대한 분석을 진행했습니다.

## 연구 내용
{current_summary[:2000]}...

## 참고 문헌
{format_sources(state.web_research_results)}

*참고: API 할당량 제한으로 인해 최종 보고서 생성이 제한되었습니다.*
"""
    
    # 최종 보고서 저장
    save_research_process(
        state,
        "Final Research Report",
        final_report
    )
    
    # 결과 반환 전 노드 완료 상태 업데이트
    update_node_status("finalize_summary", "완료", "최종 보고서 작성 완료")
    return SummaryState(
        research_topic=state.research_topic,
        running_summary=final_report,
        needs_external_info=False,
        research_loop_count=state.research_loop_count,
        web_research_results=state.web_research_results,
        search_query=state.search_query
    )

def initialize_research(state_input: SummaryStateInput, config: RunnableConfig) -> SummaryState:
    """사용자 입력으로 초기 상태 생성 및 Mind Map 초기화"""
    
    # 노드 시작 상태 업데이트
    update_node_status("initialize", "시작", f"연구 주제: {state_input.research_topic}")
    
    clear_session_files()
    
    # Configuration에서 Neo4j 설정 가져오기
    configurable = Configuration.from_runnable_config(config)
    
    # Mind Map 에이전트 초기화 및 주제별 초기화
    try:
        mind_map = MindMapAgent(
            url=configurable.neo4j_uri,
            username=configurable.neo4j_username,
            password=configurable.neo4j_password
        )
        
        # 새 연구 주제에 대한 Mind Map 초기화
        mind_map.initialize_for_topic(state_input.research_topic)
        
        # 초기 Mind Map 생성 기록
        save_research_process(
            SummaryState(research_topic=state_input.research_topic),
            "Mind Map Initialization",
            f"새 연구 주제에 대한 Mind Map 초기화: {state_input.research_topic}"
        )
    except Exception as e:
        print(f"Mind Map 초기화 중 오류: {e}")
        save_research_process(
            SummaryState(research_topic=state_input.research_topic),
            "Mind Map Initialization Error",
            f"오류: {str(e)}"
        )
    
    # 결과 반환 전 노드 완료 상태 업데이트
    initial_state = SummaryState(
        research_topic=state_input.research_topic,
        web_research_results=[],
        running_summary=None,
        needs_external_info=True,
        search_query=state_input.research_topic
    )
    
    update_node_status("initialize", "완료", f"초기화 완료: {state_input.research_topic}")
    return initial_state


# Add nodes and edges 
builder = StateGraph(SummaryState, input=SummaryStateInput, output=SummaryStateOutput)

# # dump save test code
# # 노드 추가
# builder.add_node("initialize", initialize_research)

# # 엣지 추가
# builder.add_edge(START, "initialize")
# builder.add_edge("initialize", END)

## action test code
# 노드 추가
builder.add_node("initialize", initialize_research)
builder.add_node("reason_from_sources", reason_from_sources)
builder.add_node("web_research", web_research)
builder.add_node("query_mind_map", query_mind_map)
builder.add_node("finalize_summary", format_final_report)

# 기본 흐름 엣지
# builder.set_entry_point("initialize")  # START -> initialize 대신 이 방식 사용
builder.add_edge(START, "initialize")
builder.add_edge("initialize", "reason_from_sources")
builder.add_edge("web_research", "reason_from_sources")
builder.add_edge("query_mind_map", "reason_from_sources")

# 동적 분기 처리 - 명확한 조건으로 수정
builder.add_conditional_edges(
    "reason_from_sources",
    lambda state: 
        "query_mind_map" if state.query_type == "mind_map" else
        "web_research" if state.query_type == "web_search" else
        "finalize_summary" if not state.needs_external_info else
        "reason_from_sources" ,
        {
            "query_mind_map" : "query_mind_map",
            "web_research" : "web_research",
            "finalize_summary" : "finalize_summary" ,
            "reason_from_sources" : "reason_from_sources"
        }
)

# 최종 노드
builder.add_edge("finalize_summary", END)

graph = builder.compile()