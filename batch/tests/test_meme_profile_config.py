from batch.utils.meme_profile_bundle import BundleLimits
from batch.utils.meme_profile_config import NOT_CONFIGURED_MESSAGE, PIPELINE, MemeProfilesConfig, load_config

VALUES = {
    "meme_profiles.text_model": "text-model", "meme_profiles.vision_model": "vision-model",
    "meme_profiles.num_ctx": 8192, "meme_profiles.batch_size": 50, "meme_profiles.concurrent_requests": 2,
    "meme_profiles.min_text_chars": 40, "meme_profiles.seed": 42, "meme_profiles.max_note_chars": 600,
    "meme_profiles.max_description_chars": 800, "meme_profiles.max_ocr_chars": 800,
    "ocr.confidence_min": 0.4, "ocr.lang_score_min": 0.3,
}


def test_constants():
    assert PIPELINE == "meme_profile"
    assert NOT_CONFIGURED_MESSAGE == "meme_profiles is not configured for this environment"


def test_load_config_reads_every_setting():
    config = load_config(lambda key, default=None: VALUES.get(key, default))

    assert config == MemeProfilesConfig(
        text_model="text-model", vision_model="vision-model", num_ctx=8192, batch_size=50,
        concurrent_requests=2, min_text_chars=40, seed=42, ocr_confidence_min=0.4, ocr_lang_score_min=0.3,
        limits=BundleLimits(note_chars=600, description_chars=800, ocr_chars=800))


def test_load_config_is_none_when_the_text_model_is_not_configured():
    values = {k: v for k, v in VALUES.items() if k != "meme_profiles.text_model"}

    assert load_config(lambda key, default=None: values.get(key, default)) is None
