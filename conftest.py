import sys
from pathlib import Path

# So `import confidence` works from a bare `pytest` at the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
