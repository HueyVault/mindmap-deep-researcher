import streamlit as st
import os
import threading
import time
from langchain_core.runnables import Runnable
from assistant.graph import graph
from assistant.state import SummaryStateInput, SummaryStateOutput
from assistant.configuration import Configuration
from assistant.utils import get_node_status_emoji
import json
from streamlit.runtime.scriptrunner import add_script_run_ctx

# 페이지 설정
st.set_page_config(
    page_title="연구 보조 AI",
    page_icon="🧠",
    layout="wide"
)

# 헤더 및 설명
st.title("🧠 AI 연구 보조")
st.markdown("연구하고 싶은 주제를 입력하면 AI가 웹 검색과 마인드맵을 활용하여 심층 분석 보고서를 생성합니다.")

# 세션 상태 초기화
if "node_history" not in st.session_state:
    st.session_state.node_history = []
if "current_node" not in st.session_state:
    st.session_state.current_node = None
if "research_results" not in st.session_state:
    st.session_state.research_results = None
if "is_researching" not in st.session_state:
    st.session_state.is_researching = False

# 네오포제이 설정 입력 (사이드바)
with st.sidebar:
    st.header("설정")
    
    # Streamlit 시크릿에서 Neo4j 설정 가져오기
    try:
        neo4j_uri = st.secrets["NEO4J_URI"]
        neo4j_username = st.secrets["NEO4J_USERNAME"]
        neo4j_password = st.secrets["NEO4J_PASSWORD"]
        st.success("Neo4j 설정이 시크릿에서 로드되었습니다.")
    except KeyError:
        st.warning("Neo4j 설정이 시크릿에 없습니다. 로컬 설정을 사용합니다.")
        neo4j_uri = "bolt://localhost:7687"
        neo4j_username = "neo4j"
        neo4j_password = "password"
    

    st.divider()
    
    # 검색 API 설정
    search_api = st.selectbox(
        "검색 API 선택",
        ["tavily"],
        index=0
    )
    
    # 연구 루프 설정
    max_loops = st.slider("최대 연구 루프 횟수", 1, 10, 1)

# 메인 입력 영역
with st.container():
    research_topic = st.text_area("연구 주제", height=100, placeholder="예: 한국의 지속가능한 에너지 정책과 향후 발전 방향")
    
    col1, col2 = st.columns([1, 3])
    with col1:
        submit_button = st.button("연구 시작", type="primary", use_container_width=True)
    
    st.divider()

# 결과 영역
if "research_results" not in st.session_state:
    st.session_state.research_results = None
    st.session_state.is_researching = False

# 백그라운드 연구 실행 상태 추적
if "research_thread_running" not in st.session_state:
    st.session_state.research_thread_running = False

# 연구 실행 함수
def run_research_in_background(topic):
    try:
        # 설정 객체 생성
        config = {
            "configurable": {
                "neo4j_uri": neo4j_uri,
                "neo4j_username": neo4j_username,
                "neo4j_password": neo4j_password,
                "max_web_research_loops": max_loops,
                "search_api": search_api
            }
        }
        
        # 환경 변수 설정 (API 키)
        if "TAVILY_API_KEY" in st.secrets:
            os.environ["TAVILY_API_KEY"] = st.secrets["TAVILY_API_KEY"]
        if "PERPLEXITY_API_KEY" in st.secrets:
            os.environ["PERPLEXITY_API_KEY"] = st.secrets["PERPLEXITY_API_KEY"]
        if "GOOGLE_API_KEY" in st.secrets:
            os.environ["GOOGLE_API_KEY"] = st.secrets["GOOGLE_API_KEY"]
            
        # 연구 실행
        input_state = SummaryStateInput(research_topic=topic)
        result = graph.invoke(input_state, config=config)
        
        # 결과가 딕셔너리로 반환됨 - running_summary를 키로 접근
        if 'running_summary' in result:
            st.session_state.research_results = result['running_summary']
        else:
            print(f"반환된 결과에 'running_summary' 키가 없습니다: {result}")
            st.session_state.research_results = "연구 결과를 생성하는 중에 오류가 발생했습니다. 다시 시도해 주세요."
        
        # 연구 완료 표시
        st.session_state.is_researching = False
        st.session_state.research_thread_running = False
        
    except Exception as e:
        st.session_state.research_results = f"연구 중 오류가 발생했습니다: {str(e)}"
        st.session_state.is_researching = False
        st.session_state.research_thread_running = False
        import traceback
        print(f"연구 오류 상세 정보: {traceback.format_exc()}")


# 실행 버튼 처리
if submit_button and research_topic and not st.session_state.is_researching:
    # 세션 상태 초기화
    st.session_state.node_history = []
    st.session_state.current_node = None
    st.session_state.research_results = None
    st.session_state.is_researching = True
    st.session_state.research_topic = research_topic  # 현재 주제 저장
    
    # 백그라운드에서 연구 실행 (Streamlit 컨텍스트 추가)
    research_thread = threading.Thread(
        target=run_research_in_background, 
        args=(research_topic,)
    )
    research_thread.daemon = True  # 프로세스 종료 시 함께 종료되도록 설정
    
    # 중요: 스레드에 Streamlit 컨텍스트 추가
    add_script_run_ctx(research_thread)
    
    research_thread.start()
    st.session_state.research_thread_running = True
    
    # 페이지 새로고침
    st.rerun()

# 연구 진행 중 화면 표시
if st.session_state.is_researching:
    st.write("### 연구 진행 중...")
    
    # 진행 상태 표시
    if st.session_state.get("node_history"):
        for step in st.session_state.node_history:
            emoji = get_node_status_emoji(step["node"])
            st.write(f"{emoji} **{step['timestamp']}** - {step['node']} ({step['status']})")
            if step.get("content"):
                with st.expander("상세 정보 보기"):
                    st.write(step["content"])
    
    # 연구가 진행 중이면 자동 새로고침
    if st.session_state.research_thread_running:
        st.empty()  # 빈 요소 생성 (페이지 길이 유지)
        time.sleep(2)  # 2초 대기
        st.rerun()  # 페이지 새로고침

# 결과 표시
if "research_results" in st.session_state and st.session_state.research_results:
    # 연구 진행 과정 표시 (축소된 형태)
    if st.session_state.get("node_history"):
        with st.expander("연구 진행 과정 보기", expanded=False):
            for i, step in enumerate(st.session_state.node_history):
                emoji = get_node_status_emoji(step["node"])
                st.write(f"{emoji} **{step['timestamp']}** - {step['node']} ({step['status']})")
                
    st.markdown("## 연구 결과")
    st.markdown(st.session_state.research_results)
    
    # 결과 다운로드 버튼
    topic_for_filename = st.session_state.get("research_topic", "연구결과")[:20]
    
    st.download_button(
        label="연구 결과 다운로드",
        data=st.session_state.research_results,
        file_name=f"연구결과_{topic_for_filename}.md",
        mime="text/markdown"
    )
elif "is_researching" in st.session_state and not st.session_state.is_researching:
    st.warning("연구 결과가 없습니다. 다시 시도해 주세요.")