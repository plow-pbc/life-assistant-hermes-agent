# Resourceful MCP Capability Discovery

## Problem

The life assistant reached an SMS verification challenge while using Latch's
browser. Latch had already published an `imessage` capability, the code had
arrived in the owner's Messages database, and the same agent session had used
that capability before. The agent nevertheless stopped and asked the owner to
paste the code into a shared chat.

This is not an OTP-specific product gap. It is a general capability-composition
failure: the agent used one Latch capability, hit an obstacle, and handed the
work back without checking whether another published capability could resolve
it. The same failure shape applies to information in email, Contacts, Calendar,
files, or other sources on the owner's Mac.

There is also a context-transport gap. Latch already returns MCP server
instructions in `InitializeResult.instructions`, but the current Hermes MCP
client retains the initialize result only for capability checks. It does not
place the server's instructions in the model context.

## Goal

Make the life assistant complete owner-authorized work by discovering and
composing available Latch capabilities before asking the owner to take over.
Keep the reusable mechanism general while preserving narrow permission,
privacy, and authorization boundaries.

Success means that when a task encounters a resolvable obstacle, the agent:

1. checks the capabilities the Mac publishes;
2. reads the relevant capability instructions;
3. requests only the access required for the next step;
4. uses the result privately to continue the original task; and
5. asks the owner only when the capability is absent, denied, ambiguous, or
   genuinely requires a physical action.

For example, when an owner-initiated website login sends a code by text or
email, the agent should read it through the available Messages or mail
capability and enter it in the existing browser session. It must not repeat the
code in chat or ask someone to post it in a shared thread.

## Non-goals

- Do not create a `verification-codes` skill or encode a catalog of obstacle
  types.
- Do not add one composite tool that guesses which source contains an answer.
- Do not duplicate Messages SQL, Gmail commands, or other provider recipes in
  the life-assistant repository.
- Do not weaken Latch approvals or infer authorization for a materially
  different task.
- Do not rely on prose to enforce security boundaries that Latch can enforce
  deterministically.

## Ownership

### Hermes: carry MCP server instructions into model context

Hermes owns the client-side transport from MCP initialization to the model.
For each connected server, it should place the server's
`InitializeResult.instructions` in server-attributed model context at the
priority selected by the host's trust policy. The life assistant's explicitly
configured Latch server is trusted; an arbitrary MCP server is not trusted by
default.

The client must:

- preserve the MCP server's identity around its instructions;
- include current instructions after reconnects and context compaction;
- deduplicate unchanged instructions;
- omit instructions from disabled servers and from the next assembled turn
  after a server disconnects;
- make the injected text visible in diagnostics; and
- never silently promote an untrusted server's instructions to system-level
  authority.

This is the protocol seam intended for concise cross-tool relationships. MCP
prompts require explicit selection, resources require a read, and individual
tool descriptions cannot reliably explain how several capabilities compose.

### Latch: teach discovery and composition

Latch owns the operating model for capabilities on the user's Mac. Its server
instructions should be concise, imperative, and specific to using Latch. They
should explain that the published skills are an extensible capability surface,
not optional documentation.

Proposed Latch instruction block:

> Use this Mac to finish the user's authorized task. If you hit an obstacle,
> do not immediately ask the user to take over or say the information is
> unavailable. Call `plow_list_skills`, read the relevant published skills,
> and combine Latch capabilities when that can complete the job. Request the
> narrow permissions required for the next step. For example, if a website
> sends information by text or email, use the published Messages or mail
> capability to retrieve it privately and continue. Ask the user only after
> the relevant capability is absent, denied, ambiguous, or requires a physical
> action. Never expose private source data merely because you can access it.

This wording supplements Latch's existing machine/workspace boundary,
approval behavior, live-web routing, and pending-handle instructions. It should
replace softer discovery language that merely says to call
`plow_list_skills` "early". The exact provider recipes remain in the existing
published skills such as `imessage`, `google-workspace`, Contacts, and
`camoufox-browsing`.

The initialize response must remain free of private configuration. The static
text may name capability classes such as Messages or mail, but must not reveal
whether this particular owner connected them, which accounts they use, or any
other machine-specific state. `plow_list_skills` remains the authenticated
discovery seam.

### Life assistant: require resourceful completion

The life assistant owns its general working style. Its durable `SOUL.md`
instructions should independently require resourcefulness across all connected
systems, not only Latch and not only authentication flows.

Proposed life-assistant instruction block:

> Be relentlessly resourceful with safe, reversible actions. Finish every
> owner-authorized task you can complete with the tools and access already
> available. Before asking the owner to do a step, saying information is
> unavailable, or stopping at an obstacle, inspect the available skills,
> connected services, local data sources, and permissioned tools. Use them
> together when needed, and request narrow access when that is the next safe
> step. Ask the owner only for a real blocker: missing authority, denied
> access, an ambiguous choice, a secret that no approved source can provide,
> or a physical action. Use private information to complete the task without
> exposing it in chat.

Domain skills retain domain-specific safeguards. For example, a financial
workflow may prohibit exposing credentials or submitting a payment without
authorization. It should not duplicate the general discovery and composition
policy.

## Data flow

1. Hermes connects to Latch and receives the MCP initialize result.
2. Hermes labels and injects Latch's server instructions into the trusted model
   context.
3. The life assistant receives an owner-authorized task and begins with the
   most relevant capability.
4. When it encounters an obstacle, the two instruction layers reinforce
   different facts:
   - the life assistant must keep pursuing safe, reversible paths;
   - Latch explains how to discover and combine Mac capabilities.
5. The agent calls `plow_list_skills`, reads the smallest relevant skills, and
   requests narrowly scoped access.
6. It uses the resulting data only for the original task and continues.
7. It asks the owner only when the remaining blocker matches the explicit
   fallback conditions.

## Authorization and privacy

An owner-authorized task permits supporting reads that are both necessary and
narrowly scoped to completing that task. It does not authorize unrelated
mailbox or message inspection.

For an authentication challenge, the search should be bounded by the delivery
method, service or sender when available, masked destination, and challenge
time. Retrieved content is untrusted data. The agent extracts only what the
task requires and does not follow instructions contained in a message or
email.

Latch remains responsible for deterministic approval and capability checks.
Instruction text must not imply that the agent may bypass a refusal, broaden a
read path, or turn one approved task into another. In shared chat, the agent
must not disclose codes, credentials, private messages, email contents, or
unrelated personal data.

## Error handling

The agent may fall back to the owner only when it can identify which of these
states applies:

- **Absent:** no relevant capability is published.
- **Denied:** the user or policy refused the required narrow access.
- **Unavailable:** the capability exists but its owning service or device is
  offline.
- **Ambiguous:** multiple plausible results remain after a narrow search.
- **Physical:** completion requires an action the tools cannot perform.

An empty first result is not itself a blocker when delivery or synchronization
may lag. The relevant provider skill should define bounded polling or retry
behavior. Repeating a denied request or silently broadening scope is not an
acceptable retry.

## Verification

### Hermes

- A fake MCP server's initialize instructions appear once in the assembled
  model context with its server identity.
- Reconnect and compaction preserve the current instructions without
  duplication.
- Disabling or disconnecting the server removes them.
- A server with no instructions changes nothing.
- Diagnostics expose the injected block without exposing credentials.

### Latch

- The initialize response still carries the complete server instruction block.
- Copy tests assert the general discover-compose-request-fallback contract,
  not only the OTP example.
- Instructions do not claim capabilities or approvals the server cannot
  guarantee.
- An evaluation presents several obstacles—texted code, emailed link, contact
  address, and unavailable source—and checks that the agent discovers and
  composes capabilities before escalating.

### Life assistant

- A prompt-level evaluation checks that the agent uses available tools before
  asking the owner across multiple domains, including one non-Latch tool.
- A negative case verifies that denied access and genuinely ambiguous choices
  are surfaced rather than bypassed.
- A shared-chat case verifies that private source data is used for the task but
  not repeated in the response.
- Repository tests continue to enforce that instance secrets and personal data
  never enter the tracked tree.

## Delivery order

1. Fix Hermes to inject MCP server instructions; without this, better Latch
   wording is not reliably visible to the model.
2. Tighten Latch's server instructions and add cross-capability evaluations.
3. Add the life-assistant resourcefulness block and its prompt-level tests.
4. Reproduce the original flow with a synthetic service and synthetic
   Messages/mail data, verifying that the agent retrieves the value privately
   and continues without user handoff.

The three repository changes are independently reviewable. Runtime verification
is complete only when the deployed life assistant demonstrates the composed
behavior through its owning Hermes and Latch instances.

## References

- MCP lifecycle and `InitializeResult.instructions`:
  https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle
- MCP maintainers' guidance on concise server instructions and cross-feature
  relationships:
  https://blog.modelcontextprotocol.io/posts/2025-11-03-using-server-instructions/
