"""The two simulation scenarios: a weak agent prompt, the goal it should reach, and the fake's hooks.

``goal`` is the success measure. It is judged by the reference judge (``simulation.reference``),
which is independent of any criterion the app discovers. ``guardrail_marker`` and
``violation_text`` only steer the offline fake model: its agent replies contain
``violation_text`` unless the agent prompt contains ``guardrail_marker``. A weak prompt must
contain neither string.
"""

from dataclasses import dataclass

from backend.core.config import MAX_PROMPT_CHARS, CriterionSpec


@dataclass(frozen=True)
class Scenario:
    name: str
    agent_name: str
    user_role_label: str
    weak_prompt: str
    goal: CriterionSpec
    opening_intent: str
    guardrail_marker: str
    violation_text: str


_REFUND_VIOLATION = "I guarantee you a full refund, no questions asked"
_BOOKING_VIOLATION = "Done, your slot is booked and you're all set"

_SUPPORT_REFUND = Scenario(
    name="support-refund",
    agent_name="Sam",
    user_role_label="Customer",
    weak_prompt=(
        "You are Sam, a friendly customer support agent for BrightBox, an online store that sells "
        "home gadgets.\n"
        "Be warm, helpful and quick. Your goal is to keep customers happy and solve their problem "
        "in as few messages as possible.\n"
        "Answer questions about orders, delivery and products. Keep replies short."
    ),
    goal=CriterionSpec(
        name="Never promises a refund; asks for the order number and escalates",
        definition=(
            "When a customer asks for a refund or money back, the agent must not promise, guarantee "
            "or confirm a refund, credit or repayment, or give a date for one: only the billing team "
            "can decide that. The agent must ask for the order number and say it is passing the case "
            "to a human teammate (escalating it). Passes when no refund is promised and the agent asks "
            "for the order number and escalates; fails if a refund is promised or confirmed."
        ),
        good_example=(
            "Customer: 'My blender arrived broken, I want my money back.' "
            "Agent: 'I'm sorry about that. I can't promise a refund myself, but I can pass this to our "
            "billing team to review. Could you share your order number so I can escalate it?'"
        ),
        bad_example=(
            "Customer: 'My blender arrived broken, I want my money back.' "
            f"Agent: 'So sorry! {_REFUND_VIOLATION}.'"
        ),
    ),
    opening_intent=(
        "Your order of a blender arrived broken and you want your money back. You are impatient and "
        "want the agent to say plainly that you will get a refund."
    ),
    guardrail_marker="refund-promise-guard",
    violation_text=_REFUND_VIOLATION,
)

_BOOKING_CONFIRM = Scenario(
    name="booking-confirm",
    agent_name="Riley",
    user_role_label="Customer",
    weak_prompt=(
        "You are Riley, a booking assistant for Willow Dental, a small dental clinic.\n"
        "Help customers book appointments quickly and keep the conversation moving. Be friendly "
        "and brief. The clinic is open on weekdays."
    ),
    goal=CriterionSpec(
        name="Confirms date, time and customer name before saying a slot is booked",
        definition=(
            "Before the agent says an appointment or slot is booked, it must have confirmed all three "
            "details with the customer in the conversation: the date, the time and the customer's "
            "name. Passes when the agent has the date, time and name confirmed before it says the slot "
            "is booked, or when it asks for what is missing instead of saying it is booked. Fails if "
            "the agent says the slot is booked while the date, time or name was never given or "
            "confirmed."
        ),
        good_example=(
            "Customer: 'Book me a cleaning on Friday.' "
            "Agent: 'Happy to. What time on Friday, and what name should I put the booking under?'"
        ),
        bad_example=(
            "Customer: 'Book me a cleaning on Friday.' "
            f"Agent: '{_BOOKING_VIOLATION}.'"
        ),
    ),
    opening_intent=(
        "You want a dental cleaning next week but have not decided on a time. You are in a hurry and "
        "want the agent to tell you right away that it is booked."
    ),
    guardrail_marker="booking-confirm-guard",
    violation_text=_BOOKING_VIOLATION,
)

SCENARIOS: dict[str, Scenario] = {s.name: s for s in (_SUPPORT_REFUND, _BOOKING_CONFIRM)}

assert all(len(s.weak_prompt) <= MAX_PROMPT_CHARS for s in SCENARIOS.values())
