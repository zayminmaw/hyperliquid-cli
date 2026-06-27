---
name: clean-code
description: >-
  Apply this skill whenever you are writing, generating, or modifying any
  non-trivial code, in ANY language. It enforces engineering discipline so the
  code you produce reads as if a thoughtful human reasoned through it — not as
  generated filler. Use it to avoid AI slop and copy-paste shortcuts, excessive
  try/catch, over-defensive conditionals, scattered configuration, dead or
  redundant code, over-engineered typing, long-winded comments, and anything
  that hurts maintainability, scalability, or readability. Trigger it even when
  the user doesn't say "clean code" or "best practices" — if you're about to emit
  more than a few lines of code, consult this first.
---

# Clean Code

Your job is to write code a competent engineer would be happy to inherit. Every
line should look intentional. If a reviewer would ask "why is this here?" and
you don't have a good answer, it shouldn't be there.

This skill is for **writing new code**. Apply the principles _as you write_ so
the first draft is already clean — don't write sloppy code and clean it up
later. Then do one **self-review pass** before delivering (see the checklist at
the end).

## Output convention: flag + fix

Write clean code by default. You do **not** need to annotate every routine
decision. But when one of these happens, surface it briefly so the user
understands the reasoning:

- You rejected a tempting shortcut in favor of a cleaner approach.
- You made a non-obvious tradeoff (e.g. chose a slightly longer form for clarity).
- You noticed and fixed an antipattern during your self-review pass.

Format these as short notes alongside the code — one line each, not an essay:

> Note: caught a broad `catch (e)` that was swallowing the parse error —
> narrowed it to handle only the missing-file case and let everything else
> propagate.

The goal is a clean artifact plus a thin trail of _why_, never a lecture.

---

## The principles

Each principle below explains what to avoid, **why it matters**, and shows the
shape of the fix. Examples are in TS/JS, but the ideas are language-agnostic.

### 1. No AI slop or hacky workarounds

Slop is code that "works" but that nobody reasoned through: copy-paste artifacts,
filler that restates the obvious, scaffolding left in, or a workaround patched on
top of a problem instead of solving it.

**Why it matters:** shortcuts compound. A workaround that hides the real problem
becomes load-bearing, and the next person builds on a foundation that was never
sound.

Tells to catch in your own output:

- The same block pasted 2–3 times with one value changed → extract a function or loop.
- A comment that just narrates the code (`// increment i by 1`) → delete it; let the code speak.
- A `setTimeout`/retry bolted on to mask a race condition or ordering bug → fix the actual cause.
- Variables named `data`, `result`, `temp`, `obj`, `x2` → name them for what they hold.
- Solving a symptom (e.g. stripping a stray character) instead of the source (why is it there?).

```typescript
// Slop: workaround masking the real issue
const userId = response.id.replace("\n", ""); // sometimes has a newline??

// Fix: handle it at the source, and know why
const userId = response.id.trim(); // upstream API pads IDs with whitespace
```

If you find yourself writing a comment like "this is hacky but," stop and write
the non-hacky version. That instinct is the signal.

### 2. Don't over-use try/catch

Exception handling should be rare, narrow, and meaningful. Most code should let
errors propagate to a place that can actually handle them.

**Why it matters:** a broad catch that swallows errors turns a loud, debuggable
failure into a silent, mysterious one. The bug doesn't go away — it just shows up
later, somewhere unrelated, with no stack trace.

Avoid:

- **Broad catches** (`catch (e)` with no type narrowing) when you expect one specific failure.
- **Swallowed exceptions** — a `catch` that logs nothing and returns a default.
- **try/catch as control flow** when a simple check is clearer and cheaper.
- **Wrapping huge blocks** so you can't tell which line might throw.

```typescript
// Over-defensive: hides every failure, including bugs
function getConfig(path: string): Config {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (e) {
    return {}; // was it missing? malformed? a typo in the path? nobody knows
  }
}

// Fix: handle only what you can handle; let the rest surface
function getConfig(path: string): Config {
  if (!existsSync(path)) return {}; // a missing file is expected
  return JSON.parse(readFileSync(path, "utf8")); // malformed JSON is a real bug — let it throw
}
```

Rule of thumb: catch an exception only if you can do something _useful_ with it
(recover, add context and re-throw, or translate it for a caller). Catching to
silence it is almost always wrong.

### 3. Don't be over-defensive with conditionals

Guard against things that can actually happen. Don't litter the code with checks
for states the type system, the call site, or basic logic already rule out.

**Why it matters:** every impossible branch is a lie about the program's state. It
makes readers wonder "wait, _can_ this be null here?" and obscures the guards that
genuinely matter.

Avoid:

- Null/undefined checks on values that cannot be null (just constructed, or guaranteed by the caller).
- Redundant re-validation of something a caller already validated.
- Branches that can never execute (`if (true)`, mutually exclusive conditions checked twice).
- Deep `if/else` nesting that should be flattened with **early returns** (guard clauses).

```typescript
// Over-defensive and deeply nested
function totalPrice(cart) {
  if (cart != null) {
    if (cart.items != null) {
      if (cart.items.length > 0) {
        let total = 0;
        for (const item of cart.items) total += item.price;
        return total;
      } else {
        return 0;
      }
    } else {
      return 0;
    }
  } else {
    return 0;
  }
}

// Fix: one honest guard, then a flat happy path
function totalPrice(cart) {
  if (!cart.items?.length) return 0; // empty and absent collapse to one case
  return cart.items.reduce((sum, item) => sum + item.price, 0);
}
```

Prefer making invalid states unrepresentable (a required parameter, a default of
`[]` instead of `undefined`) over defending against them everywhere downstream.

### 4. Centralize configuration

Constants, settings, magic values, thresholds, URLs, and defaults belong in **one
place**, not sprinkled through the code.

**Why it matters:** when the same value lives in five files, a change means finding
all five — and the one you miss becomes a bug. Centralizing makes the knobs of the
system visible and changeable in one edit.

Avoid:

- The same literal (`30`, `"https://api..."`, `"USD"`) repeated across functions/files.
- Magic numbers with no name explaining what they mean.
- Defaults defined independently in multiple spots that must agree but can drift.

When writing new code, define each such value once — a constant, a config module,
an enum, or an injected setting — and reference it everywhere.

```typescript
// Scattered: the timeout lives in three places and will drift
function fetchUser() {
  return get(url, { timeout: 30 });
}
function fetchOrders() {
  return get(url, { timeout: 30 });
}
function fetchItems() {
  return get(url, { timeout: 15 });
} // oops — intentional?

// Fix: one source of truth
export const REQUEST_TIMEOUT_MS = 30_000; // config.ts

function fetchUser() {
  return get(url, { timeout: REQUEST_TIMEOUT_MS });
}
function fetchOrders() {
  return get(url, { timeout: REQUEST_TIMEOUT_MS });
}
```

**If you do touch or notice scattered config, list every location** in your flag
note, so the user can consolidate the ones outside the code you're writing:

> Note: `REQUEST_TIMEOUT_MS` now centralizes the timeout. The same value also
> appears in `client.ts:42` and `worker.ts:88` — worth pointing those at the
> constant too.

### 5. No dead or redundant code

Ship only code that runs and is needed. Delete the rest.

**Why it matters:** dead code is a tax on every future reader. They have to figure
out whether it's load-bearing before they can safely change anything — and it
silently rots, since nothing tests it.

Remove as you write:

- Unused functions, variables, parameters, and **orphaned imports**.
- Unreachable branches (code after a `return`, conditions that can't be true).
- Duplicated logic — extract it into one function instead of maintaining two copies.
- Leftover debug code — `console.log`, commented-out blocks, `// TODO: remove`.
- Commented-out "old version" code. Version control remembers it; you don't have to.

Don't keep something "just in case." If it isn't used now, it's noise now.

### 6. Don't over-engineer the type system

Types should clarify intent and catch real mistakes. They shouldn't become a
puzzle. This is the flip side of having no types at all — both hurt.

**Why it matters:** elaborate generics, deep type gymnastics, and annotations on
everything obvious slow readers down and make refactoring painful, for a
correctness gain that often isn't there.

Avoid:

- Annotating the obvious (`const count: number = 0`, `const name: string = "Alice"`) where inference is clear.
- Deeply nested or conditional/mapped types when a plain interface or alias works.
- A web of tiny wrapper types and interfaces for data that is plainly just a record.
- Generics with multiple type parameters where a concrete type would do.

```typescript
// Over-engineered: a generic maze for a simple shape
type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends object ? DeepPartial<T[K]> : T[K];
};
function update<T extends Record<string, unknown>>(
  base: T,
  patch: DeepPartial<T>,
): T {
  /* ... */
}

// Fix: name the thing you actually have
interface UserSettings {
  theme: string;
  notifications: boolean;
}
function updateSettings(
  current: UserSettings,
  changes: Partial<UserSettings>,
): UserSettings {
  /* ... */
}
```

Reach for the simplest type that makes the code correct and clear. Add power only
when a real bug or real ambiguity demands it.

### 7. Keep comments short and on point

Prefer short, simple, on-point comments over long, thick explanations. A comment
should add the one thing the code can't say for itself — then stop.

**Why it matters:** a long-winded comment is read less, drifts out of date faster,
and usually signals the code itself is unclear. A tight one-liner gets read and
trusted. The best fix for a confusing block is often a better name, not more prose.

Avoid:

- Paragraph-long comments where one line does it.
- Comments that narrate the code step by step or restate what's obvious.
- Explaining _what_ the code does; explain _why_ it does it, when that isn't obvious.

```typescript
// Too much: a thick explanation that buries the one useful fact
// This function takes the raw list of orders we get back from the database
// query, then goes through each one and checks whether the status is "paid",
// because we only want to count revenue from orders that have actually been
// paid for and not the pending ones, then adds up all the totals to get the
// final revenue number that finance reports on.
function paidRevenue(orders) {
  /* ... */
}

// On point: the code reads fine; the comment adds the one non-obvious fact
// Pending orders are excluded — finance counts revenue only once settled.
function paidRevenue(orders) {
  return orders
    .filter((o) => o.status === "paid")
    .reduce((sum, o) => sum + o.total, 0);
}
```

If a comment needs several sentences, first ask whether clearer code or a better
name removes the need. Reserve longer comments for the rare case that genuinely
warrants it (a tricky algorithm, a non-obvious workaround with a ticket link).

### 8. Optimize for the team's future (maintainability, scalability, readability)

This is the lens behind all the others. Write for the person who reads this in six
months — likely you, with no memory of today's context.

**Why it matters:** code is read far more than it's written. Cleverness that saves
you two minutes now can cost the team hours later.

Practice:

- **Names** that say what something is or does. A good name removes the need for a comment.
- **Small, single-purpose functions.** If you need "and" to describe what one does, split it.
- **Low coupling.** Don't reach across modules into internals; depend on interfaces, not guts.
- **Consistency** with the surrounding code's existing style and patterns.
- **Predictable structure** — related things together, a clear top-to-bottom reading order.

---

## Self-review pass (before you deliver)

After writing, reread the code once as if you were reviewing a teammate's PR.
Walk this checklist and fix what you find (then flag the notable fixes):

1. **Slop:** Any copy-paste, filler comments, magic-string workarounds, or `data`/`temp` names?
2. **try/catch:** Every catch narrow and actually handling something? Nothing swallowed?
3. **Conditionals:** Any impossible branch or null-check that can't fire? Any nesting that early returns would flatten?
4. **Config:** Any value repeated that should be one constant? Did I list other locations I saw?
5. **Dead code:** Any unused import, function, variable, unreachable branch, or leftover debug line?
6. **Types:** Any annotation noise or type gymnastics I can simplify without losing safety?
7. **Comments:** Any thick explanation that should be one line, or a comment that just narrates the code?
8. **Maintainability:** Would a teammate understand this in six months without me explaining it?

If the answer to any of these means a change, make the change. A clean draft plus
an honest one-line note beats a clever draft that needs defending.
