def resolve_labels(source, settings) -> list[str]:
    extraction = source.extraction or {}
    labels = extraction.get("labels")
    if labels:
        return labels
    return settings.get("trends.labels", [])


def resolve_model(source, settings) -> str | None:
    extraction = source.extraction or {}
    model = extraction.get("model")
    if model:
        return model
    return settings.get("trends.model")
