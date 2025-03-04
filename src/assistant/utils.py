import os
import requests
from typing import Dict, Any
from langsmith import traceable
from tavily import TavilyClient
from assistant.state import SummaryState  # 이 줄을 추가
from datetime import datetime

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