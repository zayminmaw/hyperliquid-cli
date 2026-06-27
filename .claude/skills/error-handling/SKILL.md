---
name: error-handling
description: >
  Guides idiomatic error handling and prevents try/catch overuse when writing, reviewing,
  or refactoring code in any language. Use when code contains try/catch/except/rescue
  blocks, when the user asks to handle errors, add error handling, or make code more
  robust, when discussing exceptions, error propagation, or Result/Option types, or when
  generating non-trivial functions that could fail. Covers severity-tiered anti-patterns
  and per-language idioms (JS/TS, Python, Rust, Go, Solidity, Java/Kotlin). For restructuring
  nested if/else and defensive branching, see the clean-conditionals skill.
---

# Error Handling Skill

This skill prevents excessive or incorrect use of try/catch (and language equivalents: `except`,
`rescue`, `recover`, etc.) and guides toward idiomatic error handling.

---

## Core Philosophy

Try/catch is a last resort, not a first instinct. Before reaching for it, ask:

1. **Can this error be prevented?** Validate inputs, check preconditions, use types.
2. **Should this be an error at all?** Some "errors" are normal control flow — use return values.
3. **Who should handle this?** Most errors should propagate up, not be caught locally.
4. **What can the caller actually do?** Only catch errors you can meaningfully recover from.

---

## Severity Tiers

Use these tiers to calibrate response: prescriptive for HIGH, advisory for MEDIUM/LOW.

### 🔴 HIGH — Enforce correction, explain why

These patterns are almost always bugs:

| Anti-pattern | Why it's harmful |
|---|---|
| **Empty catch block** | Silently discards errors; makes debugging nearly impossible |
| **Catch-all + swallow** | `catch(e) {}` or `except: pass` hides failures from callers |
| **try/catch for control flow** | Using exceptions to branch logic (e.g., checking if a key exists by catching KeyError) |
| **Nested try/catch** | Usually signals the inner block should be its own function; dramatically hurts readability |
| **Re-throwing without context** | `throw e` loses stack trace in many languages; use `throw new Error("context", { cause: e })` |

**Response pattern for HIGH:** Refuse to write the anti-pattern. Provide the correct alternative
with a brief explanation. Be direct.

### 🟡 MEDIUM — Advise and offer alternatives

These patterns may be acceptable but often have better alternatives:

| Pattern | Better alternative |
|---|---|
| Catching broad base class (Exception, Error, std::exception) | Catch specific error types |
| try/catch around large blocks | Shrink the try block to the exact operation that can throw |
| Using exceptions for expected/frequent cases | Return value + null/Option/Result instead |
| Catch + log + rethrow (without adding context) | Just rethrow, or add meaningful context |

**Response pattern for MEDIUM:** Write working code, but add a comment or note explaining the
trade-off and suggesting the better pattern.

### 🟢 LOW — Note only if asked or reviewing

- Single top-level error boundary (web servers, CLI entry points) — often correct
- Wrapping third-party library calls that have inconsistent error contracts — sometimes necessary
- Retrying transient errors (network, DB) — legitimate use of catch

---

## Language-Specific Guidance

Read the relevant section when generating or reviewing code. For languages not listed, apply the
general principles and note language-idiomatic alternatives.

### JavaScript / TypeScript
- **Prefer:** `Result<T, E>` pattern (neverthrow lib), discriminated unions, or explicit `null`/`undefined` returns for expected failures
- **Avoid:** `try/catch` inside loops; async functions that swallow rejections
- **Async:** Always `await` inside try, not the whole async function body unless necessary
- **Node.js:** Error-first callbacks are legacy; prefer Promise rejections with typed errors
- **Pattern:**
  ```ts
  // ❌ Anti-pattern
  function getUser(id: string) {
    try {
      return db.find(id);
    } catch (e) {} // swallowed
  }

  // ✅ Preferred
  function getUser(id: string): User | null {
    return db.find(id) ?? null; // DB returns null if not found
  }

  // ✅ For truly exceptional cases
  function getUser(id: string): User {
    const user = db.find(id);
    if (!user) throw new UserNotFoundError(id); // typed, not generic Error
    return user;
  }
  ```

### Python
- **Prefer:** Return `None` or use `Optional[T]` for expected absence; raise specific exceptions
- **Avoid:** Bare `except:` (catches SystemExit, KeyboardInterrupt!); `except Exception as e: pass`
- **Use:** `contextlib.suppress(SpecificError)` when you genuinely want to ignore one error type
- **Pattern:**
  ```python
  # ❌ Anti-pattern
  try:
    value = my_dict[key]
  except:
    value = default  # use my_dict.get(key, default) instead

  # ✅ Preferred
  value = my_dict.get(key, default)
  ```

### Rust
- **Always use:** `Result<T, E>` and `Option<T>` — `panic!` and `unwrap()` are for unrecoverable states only
- **Prefer:** `?` operator for propagation; `thiserror` or `anyhow` for error types
- **Avoid:** `.unwrap()` in production code (use `.expect("reason")` at minimum, `?` ideally)
- **No try/catch:** Rust has no exceptions; `std::panic::catch_unwind` is almost never correct

### Go
- **Standard pattern:** Return `(T, error)` tuples; handle every error explicitly
- **Avoid:** Ignoring errors with `_`; using `panic/recover` except at package boundaries
- **Sentinel errors:** Use `errors.Is()` and `errors.As()` for comparison, not string matching
- **Wrapping:** Always `fmt.Errorf("context: %w", err)` to preserve chain

### Solidity / EVM (Web3)
- **Prefer:** `require(condition, "message")` for input validation; `revert CustomError()` for typed errors
- **Avoid:** `try/catch` on external calls unless you have a genuine recovery path (not just silencing)
- **Gas:** Empty catch blocks in Solidity still cost gas and hide failed external calls — HIGH severity
- **Pattern:**
  ```solidity
  // ❌ Anti-pattern — silently continues after failed external call
  try token.transfer(to, amount) {} catch {}

  // ✅ Let it revert, or handle explicitly
  bool success = token.transfer(to, amount);
  require(success, "Transfer failed");
  ```

### Java / Kotlin
- **Prefer:** Checked exceptions for recoverable conditions; unchecked for programming errors
- **Kotlin:** Use `runCatching` + `Result<T>` for functional style; avoid broad `catch(e: Exception)`
- **Avoid:** `catch (Exception e)` around large blocks; `printStackTrace()` without rethrowing

---

## Nested try/catch — Always Refactor

Nested try/catch is a strong signal to extract a function:

```js
// ❌ Nested — hard to follow, error handling tangled with logic
try {
  const data = JSON.parse(input);
  try {
    const result = process(data);
    try {
      save(result);
    } catch (e) { log(e); }
  } catch (e) { fallback(); }
} catch (e) { return null; }

// ✅ Each concern isolated
function parseInput(input) { ... }
function processData(data) { ... }
function persistResult(result) { ... }
```

---

## Decision Tree (use when unsure)

```
Is the error truly exceptional (unexpected, unrecoverable at this level)?
├─ No → Use return value, Option, Result type, or validation
└─ Yes → Is there a meaningful recovery action at THIS call site?
          ├─ No  → Let it propagate (don't catch)
          └─ Yes → Catch the SPECIFIC error type
                    └─ Add context before rethrowing or handle + return
```

---

## When try/catch IS correct

Don't overcorrect. These are legitimate uses:

- **Top-level boundaries:** Web request handlers, CLI main(), event loop entry points
- **Third-party APIs** with opaque error contracts
- **Retry logic** for transient failures (network, I/O)
- **Resource cleanup** (though `finally` / `using` / `defer` / `with` are usually better)
- **Translating errors** across abstraction layers (catch low-level, throw domain error)
