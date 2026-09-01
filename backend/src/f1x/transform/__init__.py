"""Transform layer: conform raw lap timing into analysis-ready facts.

Everything in `corrections`, `stints`, `validity` and `track_status` is pure — frames
in, frames out — so each rule is testable without a database. `repository` is the only
module here that touches SQL.
"""

from f1x.transform.pipeline import TransformResult, transform_session

__all__ = ["TransformResult", "transform_session"]
