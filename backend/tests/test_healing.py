import pytest

from jit_ui.browser.session import fingerprint_locators
from jit_ui.config.settings import Settings
from jit_ui.domain.models import ActionFrame, ActionSpec, ConditionSpec, ElementFingerprint, LocatorSpec, Observation, ParameterSpec, ProviderResponse, TaskRequest, WorkflowIR, WorkflowStep
from jit_ui.healing import HealingError, heal_step
from jit_ui.healing.surgical import _dismissal


class SequenceProvider:
    def __init__(self, actions, attempts=1):
        self.actions = list(actions)
        self.calls = 0
        self.attempts_total = 0
        self.remaining_attempts = None
        self.attempts = attempts
        self.instructions = []

    async def decide(self, observation, task, history, instruction=""):
        self.calls += 1
        self.attempts_total += self.attempts
        self.instructions.append(instruction)
        action = self.actions.pop(0) if self.actions else None
        return ProviderResponse(action=action, done=action is None, attempts=self.attempts, input_tokens=10, output_tokens=5, model="test")


class FakeSession:
    def __init__(self, *, elements, goal=False, goal_after_mutation=True, input_conditions=True, old_conditions=False):
        self.site = {"site_id": "shop", "start_url": "http://127.0.0.1:3000/shop", "allowed_origin": "http://127.0.0.1:3000"}
        self.elements = elements
        self.goal = goal
        self.goal_after_mutation = goal_after_mutation
        self.input_conditions = input_conditions
        self.old_conditions = old_conditions
        self.executed = []

    async def observe(self):
        return Observation(url=self.site["start_url"], elements=self.elements)

    async def verify_goal(self, _goal, _parameters):
        return self.goal, {}

    async def check_conditions(self, conditions, _parameters):
        if any(condition.kind == "goal" for condition in conditions):
            return self.goal
        if any(condition.kind == "value_equals" for condition in conditions):
            return self.input_conditions
        return self.old_conditions

    async def execute(self, action, _parameters):
        element = next(element for element in self.elements if element.index == action.target_index)
        self.executed.append(action)
        recorded = action.model_copy(deep=True)
        recorded.locators = fingerprint_locators(element)
        recorded.target_index = None
        if action.effect == "mutation":
            self.goal = self.goal_after_mutation
        conditions = []
        if action.action == "fill":
            conditions = [ConditionSpec(kind="value_equals", locator=recorded.locators[0], locators=recorded.locators, value_ref=action.value_ref, expected=action.value)]
        return ActionFrame(action=recorded, element=element, before_url=self.site["start_url"], after_url=self.site["start_url"],
                           before_hash=f"state-{len(self.executed)-1}", after_hash=f"state-{len(self.executed)}", post_conditions=conditions)


def input_contract():
    task = TaskRequest(site_id="shop", goal_id="search_filter_sort", task="Find shoes under 500", parameters={"product_name": "shoes"}, provider="fixture")
    step = WorkflowStep(step_id="query", intent="Enter product query", actions=[ActionSpec(action="fill", intent="Enter product query", value_ref="product_name", locators=[LocatorSpec(strategy="css", value="#old")])])
    workflow = WorkflowIR(site_id="shop", goal_id=task.goal_id, intent_signature="search", parameters={"product_name": ParameterSpec()}, steps=[step])
    return task, workflow


def mutation_contract():
    task = TaskRequest(site_id="admin", goal_id="mark_order_shipped", task="Ship order 1042", parameters={"order_id": "1042"}, provider="fixture")
    step = WorkflowStep(step_id="ship", intent="Mark requested order shipped", effect="mutation",
                        actions=[ActionSpec(action="click", intent="Mark requested order shipped", effect="mutation", locators=[LocatorSpec(strategy="testid", value="old-ship")])],
                        post_conditions=[ConditionSpec(kind="goal", expected=task.goal_id)])
    return task, WorkflowIR(site_id="admin", goal_id=task.goal_id, intent_signature="ship", parameters={"order_id": ParameterSpec()}, steps=[step])


def element(index, name, testid, role="button"):
    return ElementFingerprint(index=index, tag="input" if role == "textbox" else "button", role=role, name=name, testid=testid)


async def collect_event(_kind, _data):
    pass


def test_current_notice_button_is_recognized_as_optional_dismissal():
    frame = ActionFrame(action=ActionSpec(action="click", intent="Continue browsing"), element=element(1, "Continue browsing", "dismiss-notice"))
    assert _dismissal(frame)


@pytest.mark.asyncio
async def test_input_repair_uses_fresh_locators_and_preserves_workflow():
    task, workflow = input_contract()
    session = FakeSession(elements=[element(1, "Find products", "new-search", "textbox")])
    provider = SequenceProvider([ActionSpec(action="fill", target_index=1, value_ref="product_name", intent="Enter product query")])

    patched, frames = await heal_step(session, provider, task, workflow, 0, [], collect_event, lambda: False, Settings())

    assert patched.version == 2 and patched.parent_version == 1
    assert patched.steps[0].actions[0].locators[0].value == "new-search"
    assert patched.steps[0].post_conditions[0].value_ref == "product_name"
    assert workflow.version == 1 and workflow.steps[0].actions[0].locators[0].value == "#old"
    assert len(frames) == provider.calls == 1
    assert "Enter product query" in provider.instructions[0]


@pytest.mark.asyncio
async def test_popup_menu_dispatch_repair_becomes_one_logical_step():
    task, workflow = mutation_contract()
    elements = [element(1, "Dismiss notice", "notice-close"), element(2, "Order actions", "new-menu"), element(3, "Dispatch package", "new-dispatch")]
    session = FakeSession(elements=elements)
    session.site["site_id"] = "admin"
    provider = SequenceProvider([
        ActionSpec(action="click", target_index=1, intent="Dismiss blocking notice"),
        ActionSpec(action="click", target_index=2, intent="Open order actions"),
        ActionSpec(action="click", target_index=3, intent="Mark requested order shipped", effect="mutation"),
    ])

    patched, frames = await heal_step(session, provider, task, workflow, 0, [], collect_event, lambda: False, Settings())

    assert len(patched.steps) == 1 and len(patched.steps[0].actions) == 3
    assert patched.steps[0].actions[0].optional
    assert not patched.steps[0].actions[1].optional
    assert patched.steps[0].actions[2].effect == "mutation"
    assert len(frames) == provider.calls == 3


@pytest.mark.asyncio
async def test_already_applied_mutation_is_not_repeated_even_with_completed_prefix():
    task, workflow = mutation_contract()
    session = FakeSession(elements=[], goal=True)
    provider = SequenceProvider([])

    unchanged, frames = await heal_step(session, provider, task, workflow, 0, [], collect_event, lambda: False, Settings(), completed_actions=workflow.steps[0].actions)

    assert unchanged.version == workflow.version
    assert frames == [] and provider.calls == 0 and session.executed == []


@pytest.mark.asyncio
async def test_uncertain_mutation_fails_without_repeating_action():
    task, workflow = mutation_contract()
    session = FakeSession(elements=[element(1, "Dispatch package", "dispatch")], goal_after_mutation=False)
    provider = SequenceProvider([ActionSpec(action="click", target_index=1, intent="Dispatch package", effect="mutation")])

    with pytest.raises(HealingError) as error:
        await heal_step(session, provider, task, workflow, 0, [], collect_event, lambda: False, Settings())

    assert error.value.code == "MUTATION_STATE_UNCERTAIN"
    assert len(session.executed) == 1


@pytest.mark.asyncio
async def test_global_budget_callback_prevents_provider_request():
    task, workflow = input_contract()
    session = FakeSession(elements=[element(1, "Search", "search", "textbox")])
    provider = SequenceProvider([])

    async def exhausted(kind, _data):
        if kind == "provider_request":
            provider.remaining_attempts = 0

    with pytest.raises(HealingError) as error:
        await heal_step(session, provider, task, workflow, 0, [], exhausted, lambda: False, Settings())

    assert error.value.code == "MODEL_BUDGET_EXCEEDED"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_scoped_budget_counts_actual_retry_attempts_before_acting():
    task, workflow = input_contract()
    session = FakeSession(elements=[element(1, "Search", "search", "textbox")])
    provider = SequenceProvider([ActionSpec(action="fill", target_index=1, value_ref="product_name")], attempts=4)
    events = []

    async def collect(kind, data):
        events.append((kind, data))

    with pytest.raises(HealingError) as error:
        await heal_step(session, provider, task, workflow, 0, [], collect, lambda: False, Settings(max_repair_calls=3))

    assert error.value.code == "HEALING_MODEL_BUDGET_EXCEEDED"
    assert session.executed == []
    assert next(data for kind, data in events if kind == "provider_usage")["attempts"] == 4


@pytest.mark.asyncio
async def test_cancellation_after_usage_prevents_browser_action():
    task, workflow = input_contract()
    session = FakeSession(elements=[element(1, "Search", "search", "textbox")])
    provider = SequenceProvider([ActionSpec(action="fill", target_index=1, value_ref="product_name")])
    state = {"cancelled": False}

    async def cancel(kind, _data):
        if kind == "provider_usage":
            state["cancelled"] = True

    with pytest.raises(HealingError) as error:
        await heal_step(session, provider, task, workflow, 0, [], cancel, lambda: state["cancelled"], Settings())

    assert error.value.code == "CANCELLED"
    assert session.executed == []


@pytest.mark.asyncio
async def test_completed_safe_subprefix_is_retained_without_reexecution():
    task, workflow = mutation_contract()
    opener = ActionSpec(action="click", intent="Open order actions", locators=[LocatorSpec(strategy="testid", value="menu")])
    workflow.steps[0].actions.insert(0, opener)
    session = FakeSession(elements=[element(1, "Dispatch package", "dispatch")])
    provider = SequenceProvider([ActionSpec(action="click", target_index=1, intent="Dispatch package", effect="mutation")])

    patched, _frames = await heal_step(session, provider, task, workflow, 0, [], collect_event, lambda: False, Settings(), completed_actions=[opener])

    assert len(session.executed) == 1
    assert patched.steps[0].actions[0] == opener
    assert patched.steps[0].actions[1].locators[0].value == "dispatch"


@pytest.mark.asyncio
async def test_unrelated_parameter_action_is_rejected_before_execution():
    task, workflow = input_contract()
    task.parameters["max_price"] = 500
    workflow.parameters["max_price"] = ParameterSpec(type="integer")
    session = FakeSession(elements=[element(1, "Max price", "price", "textbox")])
    provider = SequenceProvider([ActionSpec(action="fill", target_index=1, value_ref="max_price")])

    with pytest.raises(HealingError) as error:
        await heal_step(session, provider, task, workflow, 0, [], collect_event, lambda: False, Settings())

    assert error.value.code == "HEALING_SCOPE_VIOLATION"
    assert session.executed == []
