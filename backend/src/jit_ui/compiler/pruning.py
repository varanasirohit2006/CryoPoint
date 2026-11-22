"""Remove only cycles explicitly recorded as safe and side-effect free."""

from jit_ui.domain.models import ActionFrame


def prune_trace(frames: list[ActionFrame]) -> list[ActionFrame]:
    kept: list[ActionFrame] = []
    for frame in frames:
        kept.append(frame.model_copy(deep=True))
        if not frame.after_hash or not frame.after_url:
            continue
        for start in range(len(kept) - 1, -1, -1):
            first = kept[start]
            if (first.before_url, first.before_hash) != (frame.after_url, frame.after_hash):
                continue
            cycle = kept[start:]
            if all(
                item.safe_to_prune
                and item.action.effect == "read"
                and not item.error
                and not item.post_conditions
                for item in cycle
            ):
                del kept[start:]
                break
    return kept
