from jit_ui.domain.models import ActionSpec, ElementFingerprint, Observation, TaskRequest
import pytest
from jit_ui.exploration import ExplorationError, validate_proposal


def test_executor_requires_explicit_model_fill_and_never_rewrites_click():
    task = TaskRequest(site_id="web", task="Enter name Ada", parameters={"name": "Ada"})
    observation = Observation(url="https://example.com/", elements=[ElementFingerprint(index=2, tag="input", role="textbox", name="Name")])
    with pytest.raises(ExplorationError):
        validate_proposal(ActionSpec(action="click", target_index=2, value="Ada"), observation, task, {"start_url": observation.url})
    action = validate_proposal(ActionSpec(action="fill", target_index=2, value="Ada"), observation, task,
                               {"start_url": observation.url})
    assert action.action == "fill" and action.value_ref == "name"


def test_general_date_picker_can_be_opened_by_click():
    task = TaskRequest(site_id="web", task="Choose departure date")
    observation = Observation(url="https://example.com/", elements=[ElementFingerprint(index=3, tag="input", role="textbox", name="Departure")])
    assert validate_proposal(ActionSpec(action="click", target_index=3), observation, task,
                             {"start_url": observation.url}).action == "click"
