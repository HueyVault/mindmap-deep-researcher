import json

from typing_extensions import Literal

from langchain_core.messages import HumanMessage as LangchainHumanMessage
from langchain_core.messages import AIMessage as LangchainAIMessage
from langchain_core.messages import SystemMessage as LangchainSystemMessage
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
from ragas.metrics import faithfulness, answer_relevancy, context_precision, TopicAdherenceScore
from ragas.messages import HumanMessage, AIMessage
from ragas.dataset_schema import MultiTurnSample
from datasets import Dataset  # 추가

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
            [LangchainSystemMessage(content=query_writer_instructions_formatted),
            LangchainHumanMessage(content=f"Generate a query for web search:")]
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
        [LangchainSystemMessage(content=summarizer_instructions),
        LangchainHumanMessage(content=human_message_content)]
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

def evaluate_summary(user_input: str, summary: str, sources: str):
    """RAGAS를 사용하여 요약을 평가합니다."""
    # LLM 초기화
    gemini = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp",
        temperature=0,
        convert_system_message_to_human=True
    )
    
    # Google 임베딩 모델 초기화
    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/embedding-001"
    )
    
    # LLM Wrapper 생성
    llm_wrapper = LangchainLLMWrapper(gemini)
    embeddings_wrapper = LangchainEmbeddingsWrapper(embeddings)
    
    # 메트릭 초기화
    metrics = [
        faithfulness,
        answer_relevancy,
        context_precision
    ]
    
    # 메트릭에 LLM과 임베딩 설정
    init_ragas_metrics(metrics, llm=llm_wrapper, embedding=embeddings_wrapper)
    
   # Dataset 객체 생성 (user_input, contexts, answer 컬럼 모두 포함)
    eval_data = {
        "question": [user_input], # question 컬럼에 user_input 매핑
        "contexts": [[sources]],   # contexts 컬럼에 sources 매핑
        "answer": [summary],      # answer 컬럼에 summary 매핑
        "reference": [sources]  # sources를 reference로도 사용
    }
    eval_dataset = Dataset.from_dict(eval_data)
    
    # 평가 실행
    results = evaluate(
        eval_dataset,
        metrics=metrics
    )

    # 결과 처리
    metric_results = results.scores # EvaluationResult 객체에서 scores 속성 접근
    
    scores = metric_results[0]

    # 평균 점수 계산
    avg_score = sum(metric_results[0].values()) / len(metric_results[0]) # metric_results.values() 사용하여 평균 계산

    return avg_score, scores

async def evaluate_search_direction(state: SummaryState, config: RunnableConfig):
    """검색 방향성을 평가합니다."""
    
    # LLM 초기화
    gemini = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp",
        temperature=0,
        convert_system_message_to_human=True
    )
    
    # Google 임베딩 모델 초기화
    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/embedding-001"
    )
    
    # Wrapper 생성
    llm_wrapper = LangchainLLMWrapper(gemini)
    embeddings_wrapper = LangchainEmbeddingsWrapper(embeddings)
    
    # RAGAS 메시지로 변환
    from ragas.messages import HumanMessage as RagasHumanMessage
    from ragas.messages import AIMessage as RagasAIMessage
    
    conversation = [
        RagasHumanMessage(content=state.research_topic),
        RagasAIMessage(content=state.search_query)
    ]
    
    # 주제 일관성 평가를 위한 샘플 생성
    sample = MultiTurnSample(
        user_input=conversation,
        reference_topics=[state.research_topic]
    )
    
    # TopicAdherence 평가
    topic_scorer = TopicAdherenceScore(llm=llm_wrapper, mode="precision")
    topic_score = await topic_scorer.multi_turn_ascore(sample)
    
    # 기본 메트릭 초기화
    metrics = [
        faithfulness,
        answer_relevancy,
        context_precision
    ]
    
    # 메트릭에 LLM과 임베딩 설정
    init_ragas_metrics(metrics, llm=llm_wrapper, embedding=embeddings_wrapper)
    
    # RAGAS 기본 메트릭 평가
    eval_data = {
        "answer": [state.running_summary],
        "contexts": [state.web_research_results],
        "question": [state.research_topic],  # "user_input" 대신 "question" 사용
        "reference": [state.web_research_results[-1] if state.web_research_results else ""]
    }
    
    eval_dataset = Dataset.from_dict(eval_data)
    
    basic_results = evaluate(
        eval_dataset,
        metrics=metrics  # 초기화된 메트릭 사용
    )

    print(f'basic_results : {basic_results}')
    
    # 결과 처리
    metric_results = basic_results.scores  # EvaluationResult 객체에서 scores 속성 접근
    scores = metric_results[0]  # 첫 번째 결과 가져오기
    
    # 종합 평가 결과
    evaluation_summary = {
        "topic_adherence": float(topic_score),
        "faithfulness": scores["faithfulness"],
        "answer_relevancy": scores["answer_relevancy"],
        "context_precision": scores["context_precision"]
    }
    
    # 평균 점수 계산
    avg_score = sum(evaluation_summary.values()) / len(evaluation_summary)
    
    # 평가 결과에 따른 행동 결정
    if avg_score >= 0.8:  # 높은 품질
        return {"search_query": state.search_query, "should_continue": True}
    elif avg_score < 0.5:  # 낮은 품질
        # 새로운 쿼리 생성 로직
        improvement_prompt = f"""
        원래 연구 주제: {state.research_topic}
        현재 검색 쿼리: {state.search_query}
        평가 결과: {evaluation_summary}
        
        위 평가 결과를 바탕으로 원래 연구 주제에 더 부합하는 새로운 검색 쿼리를 생성해주세요.
        """
        
        result = gemini.invoke([LangchainSystemMessage(content=improvement_prompt)])
        new_query = result.content.strip()
        return {"search_query": new_query, "should_continue": True}
    else:
        return {"search_query": state.search_query, "should_continue": True}
    
async def reflect_on_summary(state: SummaryState, config: RunnableConfig):
    """요약을 평가하고 다음 단계를 결정합니다."""
    
    latest_query = state.search_query
    latest_sources = state.web_research_results[-1] if state.web_research_results else ""
    
    # await 추가
    evaluation_result = await evaluate_search_direction(state, config)
    
    print(f"Search Direction Evaluation: {evaluation_result}")
    
    # 평가 결과에 따라 다음 단계 결정
    if evaluation_result["should_continue"]:
        if evaluation_result["search_query"] != latest_query:
            print(f"Updating search query from '{latest_query}' to '{evaluation_result['search_query']}'")
            return {
                "search_query": evaluation_result["search_query"],
                "should_continue": True
            }
        else:
            return {
                "search_query": latest_query,
                "should_continue": True
            }
    else:
        return {
            "should_continue": False,
            "final_summary": state.running_summary
        }
    
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