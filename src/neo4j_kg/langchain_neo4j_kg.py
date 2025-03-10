from langchain.chains import GraphCypherQAChain
from langchain_community.graphs import Neo4jGraph
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate


CYPHER_GENERATION_TEMPLATE = """Task: 그래프 데이터베이스를 쿼리하기 위한 Cypher 문을 생성합니다.
Instructions:
스키마에는 제공된 관계 유형 및 속성만 사용하세요.
제공되지 않은 다른 관계 유형이나 속성을 사용하지 마십시오.
Schema:
{schema}
Note: 답변에 설명이나 사과를 포함하지 마세요.
Cypher 문을 구성하는 것 외에 다른 질문을 할 수 있는 질문에는 응답하지 마세요.
생성된 Cypher 문을 제외한 어떤 텍스트도 포함하지 마세요.

The question is:
{question}"""

CYPHER_GENERATION_PROMPT = PromptTemplate(
    input_variables=["schema", "question"], template=CYPHER_GENERATION_TEMPLATE
)


CYPHER_QA_TEMPLATE = """당신은 훌륭하고 인간이 이해할 수 있는 답변을 만드는 데 도움을 주는 조수입니다.

정보 부분에는 답변을 구성하는 데 사용해야 하는 제공된 정보가 포함되어 있습니다.

제공된 정보는 권위가 있으므로 이를 의심하거나 내부 지식을 사용하여 수정하려고 해서는 안 됩니다.

대답을 질문에 대한 응답으로 들리게 만드십시오. 주어진 정보를 바탕으로 결과를 도출했다고 언급하지 마십시오.

예는 다음과 같습니다.

Question: Neo4j 주식을 소유한 관리자는 누구입니까?
Context:[manager:CTL LLC, manager:JANE STREET GROUP LLC]
Helpful Answer: CTL LLC, JANE STREET GROUP LLC는 Neo4j 주식을 소유하고 있습니다.

답변을 생성할 때 이 예를 따르십시오.

제공된 정보가 비어 있으면 답을 모른다고 말하세요.

Information:
{context}

Question: {question}
Helpful Answer:"""

CYPHER_QA_PROMPT = PromptTemplate(
    input_variables=["context", "question"], template=CYPHER_QA_TEMPLATE
)
print(CYPHER_GENERATION_PROMPT)
print(CYPHER_QA_PROMPT)


# favorite_movie_title = "The Matrix"  # 파라미터 값

# # 파라미터 값을 쿼리 문자열에 직접 삽입 (문자열 포맷팅 사용)
# cypher_query = f'''
# MATCH (movie:Movie {{title:"{favorite_movie_title}"}})<-[:ACTED_IN]-(actor)-[:ACTED_IN]->(rec:Movie)
# RETURN distinct rec.title as title LIMIT 20
# '''

# result = graph.query(cypher_query)

# print(result)

llm_json_mode = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash-exp",
    temperature=0,
    convert_system_message_to_human=True
)


chain = GraphCypherQAChain.from_llm(
    llm_json_mode, graph=graph, verbose=True, allow_dangerous_requests=True
)

print(chain.run("Who played in Top Gun?"))
# chain.run("How many reviews does the B000EPP56U product have?")

