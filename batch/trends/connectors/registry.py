from batch.trends.connectors.api import MeduzaConnector
from batch.trends.connectors.rss import RSSConnector

_CONNECTORS = {
    "rss": RSSConnector,
    "api": MeduzaConnector,
}


def get_connector(name: str, connector_type: str, config: dict):
    try:
        connector_cls = _CONNECTORS[connector_type]
    except KeyError:
        raise ValueError(f"Unknown connector_type: {connector_type!r}")
    return connector_cls(name, config)
