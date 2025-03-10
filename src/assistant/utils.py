import os
import requests
from typing import Dict, Any
from langsmith import traceable
from tavily import TavilyClient
from assistant.state import SummaryState  # 이 줄을 추가
import hashlib
import pickle
import os
# 시간 모듈 임포트가 필요합니다 (첫 부분에 추가)
import time
from datetime import datetime, timedelta
import streamlit as st


class RequestLimiter:
    """API 요청 빈도 제한 및 캐싱"""
    def __init__(self, cache_dir="api_cache", max_requests_per_minute=50):
        self.cache_dir = cache_dir
        self.max_requests = max_requests_per_minute
        self.request_times = []
        self.cache = {}
        
        # 캐시 디렉토리 생성
        os.makedirs(cache_dir, exist_ok=True)
        
        # 기존 캐시 로드
        try:
            for filename in os.listdir(cache_dir):
                if filename.endswith(".cache"):
                    with open(os.path.join(cache_dir, filename), "rb") as f:
                        self.cache[filename[:-6]] = pickle.load(f)
        except Exception as e:
            print(f"캐시 로드 중 오류: {e}")
    
    def _get_cache_key(self, prompt):
        """프롬프트에 대한 캐시 키 생성"""
        return hashlib.md5(prompt.encode()).hexdigest()
    
    def can_make_request(self):
        """요청 가능 여부 확인"""
        now = datetime.now()
        # 1분 이상 지난 요청 제거
        self.request_times = [t for t in self.request_times if now - t < timedelta(minutes=1)]
        return len(self.request_times) < self.max_requests
    
    def wait_for_capacity(self):
        """요청 가능할 때까지 대기"""
        while not self.can_make_request():
            time.sleep(0.5)
    
    def get_llm_response(self, llm, messages, force_refresh=False):
        """LLM 응답 가져오기 (캐시 우선)"""
        # 캐시 키 생성
        prompt = "".join(msg.content for msg in messages)
        cache_key = self._get_cache_key(prompt)
        
        # 캐시에서 찾기
        if not force_refresh and cache_key in self.cache:
            return self.cache[cache_key]
        
        # 용량 확인 및 대기
        self.wait_for_capacity()
        
        # 요청 시간 기록
        self.request_times.append(datetime.now())
        
        # LLM 호출
        try:
            response = llm.invoke(messages)
            self.cache[cache_key] = response
            
            # 캐시 저장
            try:
                with open(os.path.join(self.cache_dir, f"{cache_key}.cache"), "wb") as f:
                    pickle.dump(response, f)
            except Exception as e:
                print(f"캐시 저장 오류: {e}")
                
            return response
        except Exception as e:
            # 오류 발생 시 캐시에서 제거
            if cache_key in self.cache:
                del self.cache[cache_key]
            raise e


def deduplicate_and_format_sources(search_response, max_tokens_per_source, include_raw_content=False):
    """
    Takes either a single search response or list of responses from search APIs and formats them.
    Limits the raw_content to approximately max_tokens_per_source.
    include_raw_content specifies whether to include the raw_content from Tavily in the formatted string.
    
    Args:
        search_response: Either:
            - A dict with a 'results' key containing a list of search results
            - A list of dicts, each containing search results
            
    Returns:
        str: Formatted string with deduplicated sources
    """
    # Convert input to list of results
    if isinstance(search_response, dict):
        sources_list = search_response['results']
    elif isinstance(search_response, list):
        sources_list = []
        for response in search_response:
            if isinstance(response, dict) and 'results' in response:
                sources_list.extend(response['results'])
            else:
                sources_list.extend(response)
    else:
        raise ValueError("Input must be either a dict with 'results' or a list of search results")
    
    # Deduplicate by URL
    unique_sources = {}
    for source in sources_list:
        if source['url'] not in unique_sources:
            unique_sources[source['url']] = source
    
    # Format output
    formatted_text = "Sources:\n\n"
    for i, source in enumerate(unique_sources.values(), 1):
        formatted_text += f"Source {source['title']}:\n===\n"
        formatted_text += f"URL: {source['url']}\n===\n"
        formatted_text += f"Most relevant content from source: {source['content']}\n===\n"
        if include_raw_content:
            # Using rough estimate of 4 characters per token
            char_limit = max_tokens_per_source * 4
            # Handle None raw_content
            raw_content = source.get('raw_content', '')
            if raw_content is None:
                raw_content = ''
                print(f"Warning: No raw_content found for source {source['url']}")
            if len(raw_content) > char_limit:
                raw_content = raw_content[:char_limit] + "... [truncated]"
            formatted_text += f"Full source content limited to {max_tokens_per_source} tokens: {raw_content}\n\n"
                
    return formatted_text.strip()

def format_sources(search_results):
    """Format search results into a bullet-point list of sources.
    
    Args:
        search_results: List of search results or dict with 'results' key
        
    Returns:
        str: Formatted string with sources and their URLs
    """
    if not search_results:
        return "참고 문헌 없음"
        
    # 리스트인 경우 직접 처리
    if isinstance(search_results, list):
        formatted_sources = []
        for result in search_results:
            if isinstance(result, dict) and 'results' in result:
                # 딕셔너리 안의 results 리스트 처리
                for source in result['results']:
                    formatted_sources.append(f"* {source['title']} : {source['url']}")
            elif isinstance(result, str):
                # 문자열인 경우 그대로 추가
                formatted_sources.append(result)
        return '\n'.join(formatted_sources)
    
    # 딕셔너리인 경우 results 키로 접근
    elif isinstance(search_results, dict) and 'results' in search_results:
        return '\n'.join(
            f"* {source['title']} : {source['url']}"
            for source in search_results['results']
        )
    
    return "참고 문헌 형식 오류"

@traceable
def tavily_search(query, include_raw_content=True, max_results=3):
    """ Search the web using the Tavily API.
    
    Args:
        query (str): The search query to execute
        include_raw_content (bool): Whether to include the raw_content from Tavily in the formatted string
        max_results (int): Maximum number of results to return
        
    Returns:
        dict: Search response containing:
            - results (list): List of search result dictionaries, each containing:
                - title (str): Title of the search result
                - url (str): URL of the search result
                - content (str): Snippet/summary of the content
                - raw_content (str): Full content of the page if available"""
     
    tavily_client = TavilyClient()
    return tavily_client.search(query, 
                         max_results=max_results, 
                         include_raw_content=include_raw_content)

@traceable
def perplexity_search(query: str, perplexity_search_loop_count: int) -> Dict[str, Any]:
    """Search the web using the Perplexity API.
    
    Args:
        query (str): The search query to execute
        perplexity_search_loop_count (int): The loop step for perplexity search (starts at 0)
  
    Returns:
        dict: Search response containing:
            - results (list): List of search result dictionaries, each containing:
                - title (str): Title of the search result
                - url (str): URL of the search result
                - content (str): Snippet/summary of the content
                - raw_content (str): Full content of the page if available
    """

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": f"Bearer {os.getenv('PERPLEXITY_API_KEY')}"
    }
    
    payload = {
        "model": "sonar-pro",
        "messages": [
            {
                "role": "system",
                "content": "Search the web and provide factual information with sources."
            },
            {
                "role": "user",
                "content": query
            }
        ]
    }
    
    response = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers=headers,
        json=payload
    )
    response.raise_for_status()  # Raise exception for bad status codes
    
    # Parse the response
    data = response.json()
    content = data["choices"][0]["message"]["content"]

    # Perplexity returns a list of citations for a single search result
    citations = data.get("citations", ["https://perplexity.ai"])
    
    # Return first citation with full content, others just as references
    results = [{
        "title": f"Perplexity Search {perplexity_search_loop_count + 1}, Source 1",
        "url": citations[0],
        "content": content,
        "raw_content": content
    }]
    
    # Add additional citations without duplicating content
    for i, citation in enumerate(citations[1:], start=2):
        results.append({
            "title": f"Perplexity Search {perplexity_search_loop_count + 1}, Source {i}",
            "url": citation,
            "content": "See above for full content",
            "raw_content": None
        })
    
    return {"results": results}


# 전역 변수로 세션별 파일 경로 저장
_session_files: Dict[str, str] = {}

def save_research_process(state: SummaryState, step_name: str, step_content: str = None):
    """Save each step of the research process to a file, one file per research session.
    
    Args:
        state (SummaryState): The current state
        step_name (str): Name of the current step (e.g., 'query', 'search_results')
        step_content (str, optional): Additional content to save
    """
    try:
        # 연구 주제를 해시값으로 변환하여 세션 ID로 사용
        session_id = hash(state.research_topic)
        
        # 이 세션의 첫 저장인 경우 새 파일 생성
        if session_id not in _session_files:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            topic_slug = "".join(c if c.isalnum() else "_" for c in state.research_topic)[:30]
            filename = f"{topic_slug}_{timestamp}_research_process.md"
            
            # Streamlit Cloud에서도 작동하도록 상대 경로 사용
            os.makedirs('research_results', exist_ok=True)
            filepath = os.path.join('research_results', filename)
            _session_files[session_id] = filepath
            
            # 새 파일 생성 및 헤더 작성
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"# Research Process: {state.research_topic}\n")
                f.write(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                
            print(f"\nNew research session started. Results will be saved to: {filepath}")
        
        # 현재 세션의 파일에 내용 추가
        filepath = _session_files[session_id]
        log_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        content = f"\n## {step_name} - {log_timestamp}\n"
        if step_content:
            content += f"{step_content}\n"
        
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        # Streamlit Cloud에서 파일 접근 권한이 없을 수 있으므로 에러 처리
        print(f"로그 저장 중 오류 발생 (Streamlit Cloud에서는 정상): {e}")
        pass

def clear_session_files():
    """Clear the session files dictionary. 
    Call this when you want to force a new file for the same topic."""
    _session_files.clear()

# 노드 상태 처리 관련 함수들 추가 (파일 끝에 추가)
def update_node_status(node_name, status, content=None):
    """노드 상태 업데이트 함수
    
    Args:
        node_name (str): 현재 실행 중인 노드 이름
        status (str): 현재 상태 (예: "시작", "완료", "오류")
        content (str, optional): 노드 처리 결과 내용
    """
    if "node_history" not in st.session_state:
        st.session_state.node_history = []
    
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # 상태 정보 저장
    node_info = {
        "timestamp": timestamp,
        "node": node_name,
        "status": status,
        "content": content
    }
    
    # 세션 상태에 추가
    st.session_state.node_history.append(node_info)
    
    # 현재 실행 중인 노드 업데이트
    st.session_state.current_node = node_name
    
    # 디버그 정보 출력
    print(f"[{timestamp}] 노드 {node_name}: {status}")

def get_node_status_emoji(node_name):
    """노드 이름에 따른 이모지 반환"""
    node_emojis = {
        "initialize": "🔍",
        "reason_from_sources": "🧠", 
        "web_research": "🌐",
        "query_mind_map": "🔄",
        "finalize_summary": "📝"
    }
    return node_emojis.get(node_name, "⚙️")
    
# 전역 요청 제한기 인스턴스 생성
global_request_limiter = RequestLimiter(cache_dir="api_cache", max_requests_per_minute=45)


