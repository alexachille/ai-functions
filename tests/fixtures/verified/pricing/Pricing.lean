/-! Locked theory for the basket-total contract. -/

namespace Pricing

/-- Unit price in cents for a SKU, as reported by the pricing service.

`opaque`: a proof can learn its values only from the provenance axioms the
harness emits, one per logged tool call. -/
opaque priceCents : String → Nat

/-- Cost of one basket line: unit price times quantity. -/
def lineCents (line : String × Nat) : Nat :=
  priceCents line.1 * line.2

/-- The contract: `total` is exactly the cost of `basket`, in cents. -/
def IsTotalFor (total : Nat) (basket : List (String × Nat)) : Prop :=
  total = (basket.map lineCents).sum

end Pricing
