"""Every batch script's --status choices must be real image statuses (plus the selector 'all'), and the jobs that derive
data for recut cuts must accept cut_proposed (screenshot-recut R3b)."""
import ast
from pathlib import Path

from Storage.models import ImageStatus

BATCH = Path(__file__).resolve().parents[2] / "batch"
TAKES_CUT_PROPOSED = {"build_image_embeddings", "classify_text_heavy", "build_ocr_text_embeddings"}


def status_choices(path: Path):
    """The literal choices= list of the script's --status argument, or None when it has none."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument" and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "--status"):
            for keyword in node.keywords:
                if keyword.arg == "choices":
                    return list(ast.literal_eval(keyword.value))
            return None
    return None


def test_every_status_choice_is_an_image_status_or_all():
    allowed = set(ImageStatus.ALL) | {"all"}
    checked = 0
    for path in sorted(BATCH.glob("*.py")):
        choices = status_choices(path)
        if choices is not None:
            checked += 1
            assert set(choices) <= allowed, (path.name, choices)
    assert checked, "no --status choices found; the pattern or the folder moved"


def test_the_derivation_jobs_accept_cut_proposed():
    for name in sorted(TAKES_CUT_PROPOSED):
        assert "cut_proposed" in (status_choices(BATCH / f"{name}.py") or []), name
