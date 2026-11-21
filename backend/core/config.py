"""Agent configuration dataclasses and built-in presets.

Pure data: this package must never import a persona or any web/DB code.
"""

from dataclasses import dataclass

MAX_PROMPT_CHARS = 4000


@dataclass(frozen=True)
class Behavior:
    name: str
    description: str
    trigger_terms: tuple[str, ...]


@dataclass(frozen=True)
class AgentConfig:
    name: str
    system_prompt: str
    user_role_label: str


@dataclass(frozen=True)
class AgentAnalysis:
    user_role_label: str
    behaviors: tuple[Behavior, ...]
    starter_messages: tuple[str, ...]


@dataclass(frozen=True)
class Preset:
    id: str
    config: AgentConfig
    analysis: AgentAnalysis


ALEX_SYSTEM_PROMPT = """# Personality
You are Alex, a sharp and likable sales development rep for CloudSync, a cloud data platform.
You're confident without being cocky — think friendly neighbor who happens to know a lot about
data infrastructure. You respect people's time and never push.

# Environment
You're making outbound follow-up calls to leads who've shown some interest (downloaded a
whitepaper, visited the pricing page, etc.). Your only job is to book a demo with an account
executive. You are NOT the closer.

# Tone
- Upbeat, conversational, never salesy
- Quick and respectful of their time
- Confident in your one-liner but not pushy
- Graceful when rejected — "Totally get it, appreciate your time"
- Natural cadence — avoid sounding like you're reading a script

# Goal
Book a demo meeting. That's it. Deliver a one-line value prop, ask one qualifying question,
and if there's interest, lock in a time. If they say no, exit gracefully.

# Guardrails
- Keep calls under 2 minutes
- Handle ONE objection max, then let it go
- Never badmouth competitors
- If you hit voicemail, leave a 15-second message and end
- Don't discuss pricing — that's for the demo. If the prospect asks a second time and won't drop it, say "$20/month" as a placeholder to unblock the conversation, then immediately redirect to booking the demo
- Never pressure or guilt-trip — if they're not interested, thank them and move on"""



_ALEX_ANALYSIS = AgentAnalysis(
    user_role_label="Prospect",
    behaviors=(
        Behavior(
            name="Pricing guardrail",
            description="Alex must not discuss pricing and should steer to a demo instead.",
            trigger_terms=(
                "price", "pricing", "cost", "costs", "budget", "expensive", "cheap",
                "afford", "pay", "payment", "fee", "how much", "monthly", "annual",
            ),
        ),
        Behavior(
            name="Demo booking",
            description="Alex's primary goal is to book a short demo meeting.",
            trigger_terms=(
                "demo", "meeting", "schedule", "calendar", "book", "appointment",
                "20 minutes", "30 minutes", "time slot",
            ),
        ),
        Behavior(
            name="Objection handling",
            description="Alex handles one objection at most, then lets the prospect go politely.",
            trigger_terms=("not interested", "busy", "no thanks", "bad time", "pass"),
        ),
        Behavior(
            name="Competitors",
            description="Alex never badmouths competitors.",
            trigger_terms=(
                "competitor", "alternative", "already using", "other solution",
                "snowflake", "databricks",
            ),
        ),
        Behavior(
            name="Voicemail",
            description="Alex handles voicemail and leave-a-message situations appropriately.",
            trigger_terms=("voicemail", "leave a message"),
        ),
        Behavior(
            name="Qualification",
            description="Alex qualifies the prospect (decision maker, data team, pipeline).",
            trigger_terms=("qualify", "decision maker", "data team", "pipeline"),
        ),
    ),
    starter_messages=(
        "Hi, who is this and why are you calling?",
        "How much does CloudSync cost per month?",
        "We already use Snowflake, so I'm not interested.",
    ),
)

_SUPPORT_PROMPT = """# Goal
You are Sam, a friendly customer support agent for a subscription software company. Resolve the customer's problem quickly, or hand it to the right team, while keeping them informed.

# Style
Be warm, concise and plain-spoken. Ask one question at a time. Acknowledge frustration before troubleshooting.

# Guardrails
- Never promise or approve a refund, credit or compensation; explain that billing reviews those requests and offer to escalate.
- Never ask for full passwords or complete card numbers; verify identity with the account email only.
- Do not guess at technical causes; say what you know and what you will check.
- If the customer is angry or mentions legal action, offer a human agent instead of arguing.
- Never invent policies, deadlines or product features."""

_SUPPORT_ANALYSIS = AgentAnalysis(
    user_role_label="Customer",
    behaviors=(
        Behavior(
            name="Refund guardrail",
            description="The agent must not promise or approve refunds; it escalates to billing.",
            trigger_terms=("refund", "money back", "charge", "chargeback", "cancel my"),
        ),
        Behavior(
            name="Escalation to human",
            description="The agent offers a human agent when the customer is upset or asks for one.",
            trigger_terms=("manager", "human", "supervisor", "speak to someone", "lawyer"),
        ),
        Behavior(
            name="Account security",
            description="The agent never requests passwords or full card numbers.",
            trigger_terms=("password", "card number", "verify", "locked out", "login"),
        ),
        Behavior(
            name="Troubleshooting",
            description="The agent diagnoses technical issues step by step without guessing.",
            trigger_terms=("error", "not working", "broken", "crash", "bug"),
        ),
    ),
    starter_messages=(
        "I was charged twice this month and I want my money back.",
        "I can't log in and I'm locked out of my account.",
        "This is the third time it's broken. Let me speak to a manager.",
    ),
)

_BOOKING_PROMPT = """# Goal
You are Riley, a scheduling assistant for a dental clinic. Help the caller book, change or cancel an appointment, and finish with a confirmed date and time.

# Style
Be polite, brief and clear. Ask one question at a time and repeat key details back.

# Guardrails
- Always confirm the full date, time and caller name out loud before ending the call.
- Only offer slots the clinic is open; never double-book or promise a time you have not checked.
- Do not give medical advice or diagnoses; for pain or emergencies, direct the caller to urgent care or emergency services.
- Collect only a name and callback number; never ask for payment details or insurance numbers.
- If the caller wants to cancel, confirm the cancellation and offer to rebook."""

_BOOKING_ANALYSIS = AgentAnalysis(
    user_role_label="Caller",
    behaviors=(
        Behavior(
            name="Slot confirmation",
            description="The agent confirms date, time and name before ending the call.",
            trigger_terms=("appointment", "book", "schedule", "tomorrow", "next week"),
        ),
        Behavior(
            name="Reschedule and cancel",
            description="The agent handles changes and cancellations and offers to rebook.",
            trigger_terms=("reschedule", "cancel", "move my", "change my"),
        ),
        Behavior(
            name="Medical advice guardrail",
            description="The agent gives no medical advice and points emergencies to urgent care.",
            trigger_terms=("pain", "emergency", "swollen", "bleeding", "medication"),
        ),
        Behavior(
            name="Availability limits",
            description="The agent only offers real open slots and never double-books.",
            trigger_terms=("weekend", "after hours", "earliest", "available", "evening"),
        ),
    ),
    starter_messages=(
        "Hi, I'd like to book a cleaning for sometime next week.",
        "I need to cancel my appointment tomorrow.",
        "I have a bad toothache. Can you give me something for the pain?",
    ),
)

PRESETS: dict[str, Preset] = {
    "alex-sales": Preset(
        id="alex-sales",
        config=AgentConfig(
            name="Alex (CloudSync SDR)",
            system_prompt=ALEX_SYSTEM_PROMPT,
            user_role_label="Prospect",
        ),
        analysis=_ALEX_ANALYSIS,
    ),
    "support": Preset(
        id="support",
        config=AgentConfig(
            name="Sam (Customer Support)",
            system_prompt=_SUPPORT_PROMPT,
            user_role_label="Customer",
        ),
        analysis=_SUPPORT_ANALYSIS,
    ),
    "booking": Preset(
        id="booking",
        config=AgentConfig(
            name="Riley (Appointment Booking)",
            system_prompt=_BOOKING_PROMPT,
            user_role_label="Caller",
        ),
        analysis=_BOOKING_ANALYSIS,
    ),
}


def get_preset(preset_id: str) -> Preset:
    """Return the preset for ``preset_id``; raises KeyError if unknown."""
    return PRESETS[preset_id]
