# Concept 6 — Skill Usage Escalation: Repeat Incident Detection

## The idea

Darwin writes Skills when it sees a new class of failure. Skills are meant to be **temporary workarounds** — the SRE agent gets smarter, but the underlying product/infrastructure bug still exists. If the same Skill fires repeatedly across many incidents, that is a signal: the root cause has not been fixed and needs to be escalated beyond SRE scope.

This feature closes a second feedback loop — not into the SRE agent, but into the engineering or product organization.

---

## Signal logic

```
┌──────────────────────────────────────────────────────────────┐
│  After each incident resolution:                             │
│    if skill was applied → increment skill.usage_count        │
│    if skill.usage_count >= ESCALATION_THRESHOLD              │
│      AND no open problem_ticket for this skill               │
│    → create problem_ticket                                   │
└──────────────────────────────────────────────────────────────┘
```

**ESCALATION_THRESHOLD** (default: 3) — tunable per skill tag or globally.

The threshold is intentional: one or two uses of a skill is normal adaptation. Three or more uses of the *same* skill within a rolling window means the symptom is recurring, not a one-off.

---

## Data model

### `skills` collection (existing) — add two fields

```json
{
  "id": "skill_002_abc123",
  "name": "Diagnose Upstream Dependency Cascades via Exact Config Values",
  "usage_count": 4,
  "last_used_at": "2026-06-28T06:12:00Z",
  "ticket_created": true
}
```

### `problem_tickets` collection (new)

```json
{
  "id": "ticket_001_abc123",
  "created_at": "2026-06-28T06:12:00Z",
  "status": "open",
  "skill_id": "skill_002_abc123",
  "skill_name": "Diagnose Upstream Dependency Cascades via Exact Config Values",
  "skill_tags": ["CCF-1", "connection_pool_exhaustion", "upstream_dependency"],
  "usage_count": 4,
  "trigger_incident_ids": ["prod_031", "prod_032", "prod_051", "prod_061"],
  "summary": "Skill applied 4 times in 72 hours — recurring upstream dependency cascade. Root cause not resolved at infrastructure level.",
  "recommended_action": "File engineering ticket to fix upstream config propagation; this is not an SRE runbook problem."
}
```

---

## Where this fits in the Darwin Loop

```
Normal Darwin loop:
  low scores → Darwin evolves → Skill written → SRE agent improves

Skill escalation loop (new, outer):
  Skill usage_count >= threshold → Problem ticket created → Engineering notified
```

Darwin handles **adaptability** (SRE gets smarter). Skill escalation handles **root cause** (the product gets fixed). These are two distinct improvement surfaces.

---

## Implementation plan

### 1. Track skill usage (darwin/loop.py + agents/sre.py)

When the SRE agent loads and applies a skill to a resolution, emit an `event` of type `skill_used` with `skill_id`. The loop calls `storage.increment_skill_usage(skill_id)`.

```python
# darwin/storage.py
def increment_skill_usage(skill_id: str) -> int:
    result = db["skills"].find_one_and_update(
        {"id": skill_id},
        {"$inc": {"usage_count": 1}, "$set": {"last_used_at": utcnow()}},
        return_document=True,
    )
    return result["usage_count"]
```

### 2. Check threshold and create ticket (darwin/storage.py)

```python
ESCALATION_THRESHOLD = 3

def maybe_create_problem_ticket(skill_id: str, incident_id: str) -> dict | None:
    skill = db["skills"].find_one({"id": skill_id})
    if not skill:
        return None
    if skill.get("usage_count", 0) < ESCALATION_THRESHOLD:
        return None
    if skill.get("ticket_created"):
        return None  # already escalated

    ticket = {
        "id": f"ticket_{uuid4().hex[:8]}",
        "created_at": utcnow(),
        "status": "open",
        "skill_id": skill_id,
        "skill_name": skill["name"],
        "skill_tags": skill.get("tags", []),
        "usage_count": skill["usage_count"],
        "summary": (
            f"Skill '{skill['name']}' has been applied {skill['usage_count']} times. "
            "Recurring pattern suggests an unresolved root cause at the infrastructure or product level."
        ),
        "recommended_action": (
            "Escalate to engineering: the underlying failure class should be fixed in the product, "
            "not worked around by SRE runbooks."
        ),
    }
    db["problem_tickets"].insert_one(ticket)
    db["skills"].update_one({"id": skill_id}, {"$set": {"ticket_created": True}})
    return ticket
```

### 3. Emit event for UI (darwin/loop.py)

After `maybe_create_problem_ticket` returns a ticket, emit:

```python
{"type": "problem_ticket_created", "ticket": ticket, "skill_id": skill_id}
```

The UI can show this in the incident stream as a distinct row (e.g., red ticket icon + "🎫 Problem ticket raised for skill X").

### 4. API endpoint (api/)

```
GET /problem_tickets          → list all open tickets
GET /problem_tickets/{id}     → ticket detail + triggering incidents
PATCH /problem_tickets/{id}   → update status (open → resolved)
```

### 5. UI widget (frontend/)

Add a **PROBLEM TICKETS** panel below the Darwin Evolutions panel in the sidebar. Each ticket shows:
- Skill name and tags
- Usage count
- "Escalate to engineering →" CTA (links to the ticket detail)

---

## Demo narrative

> "Darwin writes skills to make the SRE agent smarter. But if the same skill fires repeatedly, Darwin recognizes that this is no longer an SRE problem — it's a product bug. So it automatically raises a problem ticket to the engineering team. This is self-improvement that reaches *outside* the SRE loop and into the product lifecycle."

---

## Out of scope (for now)

- Actual ticket system integration (Jira, Linear, PagerDuty) — the ticket lives in MongoDB for the demo
- Skill retirement after ticket resolution — tracked in `05_platform.md` under future work
- Per-tag threshold tuning — a single global threshold is sufficient for the demo
