"""
Vector distance and similarity score normalization for knowledge retrieval.
"""
import logging

logger = logging.getLogger(__name__)


def normalize_similarity(distance: float, metric: str = "COSINE") -> float:
    """
    Normalizes raw vector distance into a bounded similarity score in [0.0, 1.0].
    
    For COSINE distance (which ranges in [0.0, 2.0]):
        similarity = max(0.0, min(1.0, 1.0 - (distance / 2.0)))
    For EUCLIDEAN distance:
        similarity = max(0.0, min(1.0, 1.0 / (1.0 + distance)))
    For DOT_PRODUCT / other:
        similarity = max(0.0, min(1.0, (distance + 1.0) / 2.0))
        
    Rounds to 4 decimal places. Logs raw_distance and normalized_score at DEBUG level.
    """
    try:
        dist_f = float(distance)
    except (ValueError, TypeError):
        dist_f = 2.0

    metric_upper = str(metric).upper().strip() if metric else "COSINE"
    if metric_upper == "COSINE":
        score = max(0.0, min(1.0, 1.0 - (dist_f / 2.0)))
    elif metric_upper == "EUCLIDEAN":
        score = max(0.0, min(1.0, 1.0 / (1.0 + dist_f)))
    elif metric_upper == "DOT_PRODUCT":
        score = max(0.0, min(1.0, (dist_f + 1.0) / 2.0))
    else:
        score = max(0.0, min(1.0, 1.0 - (dist_f / 2.0)))

    score = round(score, 4)
    logger.debug(
        "Vector distance normalized: raw_distance=%s, metric=%s, normalized_score=%s",
        dist_f, metric_upper, score
    )
    return score
