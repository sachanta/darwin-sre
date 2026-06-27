import os
from dotenv import load_dotenv

load_dotenv()

DO_API_KEY = os.environ["DIGITAL_OCEAN_MODEL_ACCESS_KEY"]
DO_BASE_URL = "https://inference.do-ai.run/v1/"

GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
VOYAGE_MODEL = os.environ.get("VOYAGE_MODEL", "voyage-3")
VOYAGE_EMBEDDING_DIM = 1024
KB_VECTOR_INDEX = "kb_vector_idx"
KB_TOP_K = int(os.environ.get("KB_TOP_K", "3"))

MONGODB_URI = os.environ["MONGODB_URI"]
MONGODB_DB = os.environ.get("MONGODB_DB", "darwin_sre")

SRE_MODEL = os.environ.get("SRE_MODEL", "anthropic-claude-4.5-sonnet")
MUTATOR_MODEL = os.environ.get("MUTATOR_MODEL", "anthropic-claude-haiku-4.5")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "gemini-3.5-flash")

DARWIN_TRIGGER_THRESHOLD = float(os.environ.get("DARWIN_TRIGGER_THRESHOLD", "0.60"))
DARWIN_MAX_GENERATIONS = int(os.environ.get("DARWIN_MAX_GENERATIONS", "10"))
DARWIN_WINDOW_SIZE = int(os.environ.get("DARWIN_WINDOW_SIZE", "3"))

# Arize AX
ARIZE_API_KEY = os.environ.get("ARIZE_API_KEY", "")
ARIZE_SPACE_ID = os.environ.get("ARIZE_SPACE_ID", "")
ARIZE_PROJECT_NAME = os.environ.get("ARIZE_PROJECT_NAME", "darwin-sre")
