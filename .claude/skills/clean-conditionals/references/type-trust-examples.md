# Pattern 8 — Extended Examples: Don't Re-Check What the Type System Guarantees

Additional per-language examples for Pattern 8 in `SKILL.md`. Read this when reviewing
or generating Rust, Solidity, or type-hinted Python and you need a concrete reference
for redundant runtime checks.

---

## Rust — `match` arm already narrows the type

❌ Redundant (the match arm already selected `Circle`; `r` is a plain `f64`):
```rust
fn area(shape: Shape) -> f64 {
    match shape {
        Shape::Circle(r) => {
            if r > 0.0 {  // already known: r is f64, match already selected Circle
                std::f64::consts::PI * r * r
            } else {
                0.0
            }
        }
    }
}
```

✅ Clean (validate `r > 0` at construction, not at use):
```rust
fn area(shape: Shape) -> f64 {
    match shape {
        Shape::Circle(r) => std::f64::consts::PI * r * r,
    }
}
```

---

## Solidity — distinguish type guarantees from business rules

```solidity
function transfer(address recipient, uint amount) external {
    require(recipient != address(0));  // redundant if caller is typed contract
    require(amount > 0);               // legitimate — uint allows 0
    _transfer(msg.sender, recipient, amount);
}
```

`amount > 0` is legitimate — `uint` allows zero, which may be a business rule
violation. `recipient != address(0)` may or may not be redundant depending on
whether the caller is trusted typed code or an external EOA call. External
entry points are trust boundaries: keep the zero-address check there.

---

## Python — type hints + mypy already enforce the type

❌ Redundant (`mypy` already enforces this):
```python
def process(items: list[str]) -> None:
    if isinstance(items, list):   # type hint guarantees this
        for item in items:
            print(item)
```

✅ Clean:
```python
def process(items: list[str]) -> None:
    for item in items:
        print(item)
```

Caveat: this only holds if the project actually runs a type checker. In untyped
or partially typed Python codebases, `isinstance` checks at module boundaries
can be legitimate trust-boundary validation.
