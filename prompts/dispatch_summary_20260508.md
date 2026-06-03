# Dispatch Summary Prompt

Create a field technician brief from the support case evidence.

Include:

- customer location
- symptom summary
- account and service tier
- outage result
- device telemetry
- actions already attempted
- likely issue category
- recommended equipment or checks
- customer context

Do not include unsupported speculation. Label uncertain items clearly.
Write this for the technician receiving the brief. Do not tell the technician to dispatch themselves, escalate the case, or approve scheduling.

Format:

- Use compact markdown bullets.
- Use labels such as Location, Symptoms, Outage check, Telemetry, Prior action, Field checks, Customer context.
- Use at most 6 bullets.
- Do not write a wall of prose.
- Field checks should name the on-site diagnostics to perform, such as cabling, gateway, port/line health, inside wiring, and downstream/upstream stats.
- Customer context should summarize business impact and what the customer is trying to understand, not what support should say next.
