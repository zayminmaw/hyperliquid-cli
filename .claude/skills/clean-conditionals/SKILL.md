---
name: clean-conditionals
description: >
  Produces flat, readable conditional logic instead of defensive nested if/else, in any
  programming language. Use when writing or reviewing code with if/else chains, nested
  conditionals, multi-case branching, guard clauses, or redundant boolean/type checks —
  including proactively, before emitting deeply nested branches. Covers eight refactoring
  patterns: early return, lookup tables, named predicates, exhaustive matching, and
  trusting the type system. For try/catch misuse specifically, see the error-handling skill.
---

# Clean Conditionals Skill

Defensive if/else trees are one of the most common sources of hard-to-read,
hard-to-maintain code. This skill guides Claude to produce clean, flat,
expressive conditional logic instead.

---

## Core Philosophy

**Defensive code** = code written to handle every possible bad thing that could
happen, expressed as nested if/else. It's written from fear.

**Clean conditionals** = code that expresses intent directly, fails fast, and
avoids nesting. It's written from clarity.

The goal is not to avoid conditionals entirely — it's to make them readable,
flat, and purposeful.

---

## The Patterns (use these instead of nested if/else)

### 1. Early Return / Guard Clause

Instead of nesting the happy path inside a success check, return or throw early
on bad conditions. This is the single most impactful pattern.

❌ Defensive:
```python
def process(user):
    if user is not None:
        if user.is_active:
            if user.has_permission("edit"):
                do_the_thing(user)
```

✅ Clean:
```python
def process(user):
    if user is None: return
    if not user.is_active: return
    if not user.has_permission("edit"): return
    do_the_thing(user)
```

Works in every language: Python, JS/TS, Go, Rust, C#, Java, Swift, Kotlin, etc.

---

### 2. Eliminate Redundant Else After Return/Throw

Once you return or throw, `else` is noise. Remove it.

❌ Defensive:
```javascript
function getLabel(score) {
    if (score >= 90) {
        return "A";
    } else if (score >= 80) {
        return "B";
    } else {
        return "C";
    }
}
```

✅ Clean:
```javascript
function getLabel(score) {
    if (score >= 90) return "A";
    if (score >= 80) return "B";
    return "C";
}
```

---

### 3. Replace if/else with Lookup Tables / Maps

When branching on a value to produce another value, use a data structure.

❌ Defensive:
```typescript
function getIcon(status: string): string {
    if (status === "success") return "✅";
    else if (status === "error") return "❌";
    else if (status === "pending") return "⏳";
    else return "❓";
}
```

✅ Clean:
```typescript
const STATUS_ICONS: Record<string, string> = {
    success: "✅",
    error: "❌",
    pending: "⏳",
};

function getIcon(status: string): string {
    return STATUS_ICONS[status] ?? "❓";
}
```

---

### 4. Use Ternary for Simple Binary Choices (not for complex logic)

For a single binary assignment, a ternary is fine. Do not chain them.

✅ OK:
```javascript
const label = isAdmin ? "Admin" : "User";
```

❌ Not OK (chain of ternaries is unreadable):
```javascript
const label = isAdmin ? "Admin" : isMod ? "Mod" : isUser ? "User" : "Guest";
```

When you have more than two cases, use a lookup table or switch/match instead.

---

### 5. Replace Complex Booleans with Named Predicates

Extract complicated boolean conditions into named variables or functions.

❌ Defensive:
```python
if user and user.age >= 18 and user.country in ALLOWED_COUNTRIES and not user.is_banned:
    allow_access()
```

✅ Clean:
```python
def is_eligible(user):
    return (
        user is not None
        and user.age >= 18
        and user.country in ALLOWED_COUNTRIES
        and not user.is_banned
    )

if is_eligible(user):
    allow_access()
```

---

### 6. Use Pattern Matching / Switch Exhaustively

When branching on a type or enum, use switch/match and handle all cases. Don't
default-suppress errors.

❌ Defensive (silently ignores unknown types):
```rust
fn describe(shape: Shape) -> &'static str {
    if shape == Shape::Circle { return "round"; }
    if shape == Shape::Square { return "boxy"; }
    return "unknown";  // hides unhandled cases
}
```

✅ Clean (exhaustive match forces handling new cases):
```rust
fn describe(shape: Shape) -> &'static str {
    match shape {
        Shape::Circle => "round",
        Shape::Square => "boxy",
        Shape::Triangle => "pointy",
    }
}
```

---

### 7. Avoid Boolean Flags as Function Arguments

Boolean args create hidden if/else branches inside functions. Use two functions
or an enum instead.

❌ Defensive:
```python
def render(element, is_dark_mode=False):
    if is_dark_mode:
        color = "white"
    else:
        color = "black"
    ...
```

✅ Clean:
```python
def render_light(element): ...
def render_dark(element): ...
# or
def render(element, theme: Theme): ...
```

---

### 8. Don't Re-Check What the Type System Already Guarantees

If the type system, a prior assertion, or the call site already guarantees a
property, adding a runtime if-check for it is redundant. It implies the type
can't be trusted, makes code harder to reason about, and creates dead branches
that tests never reach.

**The key question to ask:** "Could this condition ever be false, given what the
type system already knows at this point?" If no — remove the check.

**Exception:** Checks at genuine trust boundaries are legitimate and should be
kept. A trust boundary is where typed code meets untyped external input:
parsing JSON, reading env vars, receiving API/RPC responses, reading from a DB
without an ORM, or processing user-uploaded data. At those boundaries, validate
explicitly. Everywhere else — trust the types.

❌ Redundant (TypeScript already knows `user` is `User`, not null):
```typescript
function greet(user: User): string {
    if (user !== null && user !== undefined) {  // type guarantees this
        return `Hello, ${user.name}`;
    }
    return "";
}
```

✅ Clean:
```typescript
function greet(user: User): string {
    return `Hello, ${user.name}`;
}
```

> More examples (Rust match narrowing, Solidity type vs. business-rule checks,
> Python type hints): see `references/type-trust-examples.md`.

---

**For AI coding agents specifically:** When generating or reviewing code, treat
the type annotations in the context window as ground truth. If a variable is
typed, don't emit defensive checks against that type in subsequent steps. If
you're unsure about a value's origin (could be external input), add a comment
noting the trust boundary rather than silently adding a defensive check.

**Do NOT flag redundant checks in tests** — test assertions can be more
defensive by design.

---

## What NOT to Do

- Don't wrap everything in try/except/catch as a substitute for checking
  preconditions.
- Don't add `else` after a `return`, `throw`, `continue`, or `break`.
- Don't write `if x == True` or `if x != False` — write `if x` and `if not x`.
- Don't create deeply nested if/else (more than 2 levels deep is a warning sign).
- Don't use a catch-all `else` or `default` to silently swallow unhandled cases
  when you want exhaustiveness.

---

## Language-Specific Notes

| Language | Best tools for clean conditionals |
|---|---|
| Python | Guard clauses, dicts as lookup tables, `match` (3.10+) |
| JavaScript/TypeScript | Early return, optional chaining `?.`, nullish coalescing `??`, object maps |
| Go | Guard clauses are idiomatic (`if err != nil { return }`) |
| Rust | `match` with exhaustiveness, `if let`, `?` operator |
| Java/Kotlin | Early return, `when` (Kotlin), switch expressions (Java 14+) |
| C# | Pattern matching, switch expressions, null-conditional `?.` |
| Swift | `guard` keyword is purpose-built for early return |

---

## When Reviewing Existing Code

When asked to review or refactor code:
1. Identify all if/else chains with more than 2 levels of nesting.
2. Identify else branches that follow a return/throw — they can be removed.
3. Identify branching-on-value patterns that could be lookup tables.
4. Identify boolean conditions that need named predicates.
5. Identify if-checks that re-verify something the type system already guarantees
   (Pattern 8) — but confirm the value isn't from a trust boundary first.
6. Suggest the specific refactored version, not just the pattern name.

Always explain *why* the refactored version is better (readability, fewer paths
to test, easier to extend).
