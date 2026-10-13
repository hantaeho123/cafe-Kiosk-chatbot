"""경로와 환경 변수 설정. 값은 프로젝트 루트의 .env 파일로 바꿀 수 있다 (.env.example 참고)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
KIOSK_DIR = DATA_DIR / "kiosk"
IMAGES_DIR = DATA_DIR / "images"
DOCS_PATH = DATA_DIR / "rag" / "documents.jsonl"
INDEX_DIR = DATA_DIR / "rag" / "index"
WEB_DIR = ROOT / "web"


def _flag(name, default):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-mini")
EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
# 추론 모델(gpt-5 계열 등)은 temperature 를 받지 않는다. 그럴 때는 .env 에 OPENAI_TEMPERATURE= 로 비워 둔다.
_temperature = os.getenv("OPENAI_TEMPERATURE", "0.3").strip()
TEMPERATURE = float(_temperature) if _temperature else None

RAG_TOP_K = int(os.getenv("RAG_TOP_K", "6"))
# 코사인 유사도가 이 값보다 낮은 문서는 버린다. 0 이면 끈다. scripts/eval_retrieval.py 로 점수를 보고 정한다.
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0"))
USE_MODERATION = _flag("USE_MODERATION", "true")
MODERATION_MODEL = os.getenv("OPENAI_MODERATION_MODEL", "omni-moderation-latest")

# 공개 주소로 올렸을 때의 보호 장치.
# ACCESS_PASSWORD 를 넣으면 키오스크와 직원 화면 모두 브라우저가 비밀번호를 묻는다(아이디는 아무거나). 비워 두면 묻지 않는다.
ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "").strip()
# 대화 횟수 제한(OpenAI 요금 보호). 0 이면 끈다. 한 손님(세션)이 10분 동안, 서버 전체가 1시간 동안 보낼 수 있는 메시지 수.
CHAT_LIMIT_PER_SESSION = int(os.getenv("CHAT_LIMIT_PER_SESSION", "40"))
CHAT_LIMIT_PER_HOUR = int(os.getenv("CHAT_LIMIT_PER_HOUR", "600"))
MAX_SESSIONS = int(os.getenv("MAX_SESSIONS", "200"))  # 이보다 많아지면 가장 오래 쓰지 않은 세션부터 지운다

HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "8"))  # 모델에게 다시 보여 줄 최근 대화 턴 수
MAX_TOOL_ROUNDS = 5

# 조작이 없을 때 "시간 연장" 안내를 띄우기까지의 시간(초). 비워 두면 data/kiosk/policies.json 의 값을 쓰고, 0 이면 끈다.
_idle = os.getenv("IDLE_SECONDS", "").strip()
IDLE_SECONDS = int(_idle) if _idle else None
