# Considerations

This document captures design decisions and trade-offs discussed during planning. It is not implementation spec — it is context for why things are built the way they are.

---

## Business-logic breaks and opt-in attributes

### The problem

Some breaks are invisible to the OTel agent because every technical signal looks healthy:

- A rate limiter returns `200 OK` in 2ms — no latency spike, no error, no exception
- But the business rule is broken: it is letting everyone through when it should be blocking

The agent instruments the mechanics of a request. It cannot know what the outcome means to the business.

### The opt-in fix

The customer adds a small number of SDK calls in their own code to emit a custom span attribute:

```python
span.set_attribute("ratelimit.allowed", True)
span.set_attribute("ratelimit.remaining", 0)
```

This attribute rides inside the existing span — no new signal type, no new endpoint. It flows through the same OTLP pipe into the same `spans` table.

### The problem with per-service attributes

Every service has a different definition of "business broken":

| Service | Attribute | What "broken" looks like |
|---|---|---|
| Rate limiter | `ratelimit.allowed = true` | allow-rate spikes above baseline |
| URL shortener | `redirect.found = false` | miss-rate spikes above baseline |
| Payment service | `payment.status = "declined"` | decline-rate spikes |
| Auth service | `auth.result = "fail"` | failure-rate spikes |
| Cart service | `cart.items_added` | drops to near zero |

### How the platform handles this without per-service integrations

The platform does not need to understand what an attribute means. It baselines the normal distribution of that attribute's value and flags when it drifts — same detection engine regardless of the attribute name.

The customer specifies which attributes to watch via a per-tenant config:

```yaml
watch_attributes:
  - name: ratelimit.allowed
    type: boolean
  - name: redirect.found
    type: boolean
  - name: payment.status
    match: "declined"
```

The detection rule is the same for all of them: rate of this value vs baseline rate of this value. Only the attribute name changes.

### What this requires us to build

1. **Ingest** — store all span attributes in a `Map` column in the ClickHouse spans table (ClickHouse's equivalent of JSONB)
2. **Tenant config** — a `watch_attributes` list stored per tenant in SQLite (new table)
3. **Detect** — for each watched attribute, compute its rate/distribution and compare to baseline — same logic as latency detection, different column

### The honest product boundary

This is a real source-code change on the customer's side. It is small and additive, but it exists. The product claim is:

- **Zero code for technical break detection** — true, covered by the three env vars alone
- **Minimal, additive code for business-logic detection** — a few `set_attribute` calls per signal the customer wants to expose, no structural changes to their service

| Break type | Customer code change |
|---|---|
| DB query slow | None |
| Downstream API erroring | None |
| Latency regression | None |
| Throughput collapse | None |
| Exception spike | None |
| Rate limiter broken (fast 200s) | Add `span.set_attribute(...)` |
| URL shortener miss-rate spike | Add `span.set_attribute(...)` |
| Payment decline-rate spike | Add `span.set_attribute(...)` |

We cannot claim "no code changes ever" for the full feature set. We can claim zero code for technical break detection, and minimal additive code for business-logic detection.

### When to build this

Not now. This comes after the core `ingest → detect → report` path is proven end to end. The one thing to do early is store span attributes as `JSONB` in the spans table so the data is already there when we get to this layer.
