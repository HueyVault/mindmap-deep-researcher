from neo4j import GraphDatabase
import os
from dotenv import load_dotenv

load_dotenv()
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

try:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity() # 연결 확인 추가
    print("Neo4j 연결 성공!")
except Exception as e:
    print(f"Neo4j 연결 실패: {e}")
    print(f"오류 메시지: {e}")
finally:
    if driver:
        driver.close()