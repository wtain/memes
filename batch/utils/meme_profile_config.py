"""Settings for the meme profile jobs (P2 spec, decision 2)."""
from dataclasses import dataclass

from batch.utils.meme_profile_bundle import BundleLimits

PIPELINE = "meme_profile"
NOT_CONFIGURED_MESSAGE = "meme_profiles is not configured for this environment"


@dataclass(frozen=True)
class MemeProfilesConfig:
    text_model: str
    vision_model: str
    num_ctx: int
    batch_size: int
    concurrent_requests: int
    min_text_chars: int
    seed: int
    ocr_confidence_min: float
    ocr_lang_score_min: float
    limits: BundleLimits


def load_config(get) -> MemeProfilesConfig | None:
    """`get` is `settings.get` (dotted key, optional default). None when meme_profiles.text_model is unset,
    which is how `it` (no description pipeline, out of scope) opts out cleanly."""
    text_model = get("meme_profiles.text_model")
    if not text_model:
        return None
    return MemeProfilesConfig(
        text_model=text_model,
        vision_model=get("meme_profiles.vision_model"),
        num_ctx=get("meme_profiles.num_ctx"),
        batch_size=get("meme_profiles.batch_size"),
        concurrent_requests=get("meme_profiles.concurrent_requests"),
        min_text_chars=get("meme_profiles.min_text_chars"),
        seed=get("meme_profiles.seed"),
        ocr_confidence_min=get("ocr.confidence_min"),
        ocr_lang_score_min=get("ocr.lang_score_min"),
        limits=BundleLimits(
            note_chars=get("meme_profiles.max_note_chars"),
            description_chars=get("meme_profiles.max_description_chars"),
            ocr_chars=get("meme_profiles.max_ocr_chars"),
        ),
    )
