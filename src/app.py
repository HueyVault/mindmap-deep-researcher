import streamlit as st
import os
from langchain_core.runnables import Runnable
from assistant.graph import graph
from assistant.state import SummaryStateInput, SummaryStateOutput
from assistant.configuration import Configuration
import json

# 페이지 설정
st.set_page_config(
    page_title="연구 보조 AI",
    page_icon="🧠",
    layout="wide"
)

# 헤더 및 설명
st.title("🧠 AI 연구 보조")
st.markdown("연구하고 싶은 주제를 입력하면 AI가 웹 검색과 마인드맵을 활용하여 심층 분석 보고서를 생성합니다.")

# 네오포제이 설정 입력 (사이드바)
with st.sidebar:
    st.header("설정")
    
    # # Streamlit Cloud의 secrets에서 환경 변수 가져오기 시도
    # neo4j_uri = st.text_input("Neo4j URI", value=st.secrets.get("neo4j_uri", "bolt://localhost:7687"))
    # neo4j_username = st.text_input("Neo4j 사용자명", value=st.secrets.get("neo4j_username", "neo4j"))
    # neo4j_password = st.text_input("Neo4j 비밀번호", type="password", value=st.secrets.get("neo4j_password", ""))
    
    st.divider()
    
    # 검색 API 설정
    search_api = st.selectbox(
        "검색 API 선택",
        ["tavily", "perplexity"],
        index=0
    )
    
    # 연구 루프 설정
    max_loops = st.slider("최대 연구 루프 횟수", 1, 10, 6)

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

# 연구 실행 함수
def run_research(topic):
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
        
        return result.running_summary
    except Exception as e:
        st.error(f"연구 중 오류가 발생했습니다: {str(e)}")
        return None

# 실행 버튼 처리
if submit_button and research_topic and not st.session_state.is_researching:
    with st.spinner("AI가 연구를 진행하고 있습니다... (몇 분 정도 소요될 수 있습니다)"):
        st.session_state.is_researching = True
        st.session_state.research_results = run_research(research_topic)
        st.session_state.is_researching = False
    st.experimental_rerun()

# 결과 표시
if st.session_state.research_results:
    st.markdown("## 연구 결과")
    st.markdown(st.session_state.research_results)
    
    # 결과 다운로드 버튼
    result_text = st.session_state.research_results
    st.download_button(
        label="연구 결과 다운로드",
        data=result_text,
        file_name=f"연구결과_{research_topic[:20]}.md",
        mime="text/markdown"
    )