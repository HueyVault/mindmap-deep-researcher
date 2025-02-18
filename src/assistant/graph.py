import json

from typing_extensions import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.outputs import LLMResult, ChatGeneration

# from langchain_ollama import ChatOllama
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langgraph.graph import START, END, StateGraph

from assistant.configuration import Configuration, SearchAPI
from assistant.utils import deduplicate_and_format_sources, tavily_search, format_sources, perplexity_search
from assistant.state import SummaryState, SummaryStateInput, SummaryStateOutput
from assistant.prompts import query_writer_instructions, summarizer_instructions, reflection_instructions

from ragas import evaluate
from ragas.llms import LangchainLLMWrapper 
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.run_config import RunConfig
from ragas.metrics import faithfulness, answer_relevancy, context_precision

# Nodes   
def generate_query(state: SummaryState, config: RunnableConfig):
    """ Generate a query for web search """
    
    query_writer_instructions_formatted = (
        query_writer_instructions.format(research_topic=state.research_topic) +
        "\nYou must respond in the following JSON format only: {\"query\": \"your generated query here\"}"
    )

    configurable = Configuration.from_runnable_config(config)
    llm_json_mode = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp",  # gemini-2.0-flash-exp는 아직 공개적으로 사용할 수 없을 수 있습니다
        temperature=0,
        convert_system_message_to_human=True
    )
    
    try:
        result = llm_json_mode.invoke(
            [SystemMessage(content=query_writer_instructions_formatted),
            HumanMessage(content=f"Generate a query for web search:")]
        )   
        
        # JSON 파싱 시도
        try:
            query = json.loads(result.content)
        except json.JSONDecodeError:
            # JSON 파싱 실패 시 직접 JSON 형식으로 변환
            return {"search_query": result.content.strip()}
            
        return {"search_query": query.get('query', result.content.strip())}
    except Exception as e:
        print(f"Error in generate_query: {e}")
        return {"search_query": state.research_topic}

def web_research(state: SummaryState, config: RunnableConfig):
    """ Gather information from the web """
    
    # Configure 
    configurable = Configuration.from_runnable_config(config)

    # Handle both cases for search_api:
    # 1. When selected in Studio UI -> returns a string (e.g. "tavily")
    # 2. When using default -> returns an Enum (e.g. SearchAPI.TAVILY)
    if isinstance(configurable.search_api, str):
        search_api = configurable.search_api
    else:
        search_api = configurable.search_api.value

    # Search the web
    if search_api == "tavily":
        search_results = tavily_search(state.search_query, include_raw_content=True, max_results=1)
        search_str = deduplicate_and_format_sources(search_results, max_tokens_per_source=1000, include_raw_content=True)
    elif search_api == "perplexity":
        search_results = perplexity_search(state.search_query, state.research_loop_count)
        search_str = deduplicate_and_format_sources(search_results, max_tokens_per_source=1000, include_raw_content=False)
    else:
        raise ValueError(f"Unsupported search API: {configurable.search_api}")
        
    return {"sources_gathered": [format_sources(search_results)], "research_loop_count": state.research_loop_count + 1, "web_research_results": [search_str]}

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
    # ChatOllama를 ChatGoogleGenerativeAI로 변경
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp",
        temperature=0,
        convert_system_message_to_human=True
    )
    result = llm.invoke(
        [SystemMessage(content=summarizer_instructions),
        HumanMessage(content=human_message_content)]
    )

    running_summary = result.content

    # Deepseek 모델 관련 hack 부분은 Gemini에서는 필요 없을 수 있지만, 
    # 안전을 위해 유지
    while "<think>" in running_summary and "</think>" in running_summary:
        start = running_summary.find("<think>")
        end = running_summary.find("</think>") + len("</think>")
        running_summary = running_summary[:start] + running_summary[end:]

    return {"running_summary": running_summary}

def init_ragas_metrics(metrics, llm, embedding=None):
    """RAGAS 메트릭 초기화"""
    for metric in metrics:
        if hasattr(metric, 'llm'):
            metric.llm = llm
        if hasattr(metric, 'embeddings') and embedding:
            metric.embeddings = embedding
        run_config = RunConfig()
        metric.init(run_config)

def evaluate_summary(summary: str, sources: str):
    """RAGAS를 사용하여 요약을 평가합니다."""
    # LLM 초기화
    gemini = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp",
        temperature=0,
        convert_system_message_to_human=True
    )
    
    gemini_embeddings = GoogleGenerativeAIEmbeddings(model="text-embedding-004")
    # LLM Wrapper 생성
    llm_wrapper = LangchainLLMWrapper(gemini)
    embeddings_wrapper = LangchainEmbeddingsWrapper(gemini_embeddings)

    # 메트릭 초기화
    metrics = [
        faithfulness,
        answer_relevancy,
        context_precision
    ]
    
    # 메트릭에 LLM 설정
    init_ragas_metrics(metrics, llm=llm_wrapper, embedding=embeddings_wrapper)

    
    # 평가 데이터셋 생성
    eval_dataset = [{"answer": summary, "contexts": [sources]}]
    
    # 평가 실행
    results = evaluate(
        eval_dataset,
        metrics=metrics
    )
    
    # 평균 점수 계산
    scores = results.to_dict()
    avg_score = sum(scores.values()) / len(scores)
    return avg_score, scores

def reflect_on_summary(state: SummaryState, config: RunnableConfig):
    """요약을 평가하고 다음 단계를 결정합니다."""
    
    # RAGAS 평가 수행
    latest_sources = state.web_research_results[-1] if state.web_research_results else ""
    avg_score, scores = evaluate_summary(state.running_summary, latest_sources)
    
    # 평가 결과를 기록
    print(f"RAGAS Evaluation Scores: {scores}")
    print(f"Average Score: {avg_score}")
    
    # 평가 점수에 따른 로직
    if avg_score >= 0.8:  # 높은 품질
        return {"search_query": None, "should_continue": False}
    elif avg_score < 0.5:  # 낮은 품질
        # 원래의 research_topic으로 새로운 쿼리 생성
        return {"search_query": f"Detailed information about {state.research_topic}", 
                "should_continue": True}
    else:  # 중간 품질 - 기존 로직 사용
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
                
            return {"search_query": query, "should_continue": True}
        except Exception as e:
            print(f"Error in reflect_on_summary: {e}")
            return {"search_query": f"Tell me more about {state.research_topic}", 
                    "should_continue": True}
    
def finalize_summary(state: SummaryState):
    """ Finalize the summary """
    
    # Format all accumulated sources into a single bulleted list
    all_sources = "\n".join(source for source in state.sources_gathered)
    state.running_summary = f"## Summary\n\n{state.running_summary}\n\n ### Sources:\n{all_sources}"
    return {"running_summary": state.running_summary}

def route_research(state: SummaryState, config: RunnableConfig) -> Literal["finalize_summary", "web_research"]:
    """RAGAS 평가 결과를 기반으로 연구 방향을 결정합니다."""
    
    configurable = Configuration.from_runnable_config(config)
    
    # reflect_on_summary에서 반환된 should_continue 확인
    if (hasattr(state, 'search_query') and 
        state.search_query is None):  # 높은 품질로 판단됨
        return "finalize_summary"
    
    if state.research_loop_count <= configurable.max_web_research_loops:
        return "web_research"
    else:
        return "finalize_summary"
    
# Add nodes and edges 
builder = StateGraph(SummaryState, input=SummaryStateInput, output=SummaryStateOutput, config_schema=Configuration)
builder.add_node("generate_query", generate_query)
builder.add_node("web_research", web_research)
builder.add_node("summarize_sources", summarize_sources)
builder.add_node("reflect_on_summary", reflect_on_summary)
builder.add_node("finalize_summary", finalize_summary)

# Add edges
builder.add_edge(START, "generate_query")
builder.add_edge("generate_query", "web_research")
builder.add_edge("web_research", "summarize_sources")
builder.add_edge("summarize_sources", "reflect_on_summary")
builder.add_conditional_edges("reflect_on_summary", route_research)
builder.add_edge("finalize_summary", END)

graph = builder.compile()