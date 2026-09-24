/-! # Insertion position in a sorted list

The contract says where a key belongs in a sorted list, and nothing about how
to find that place: any correct search algorithm satisfies it. -/

namespace Lookup

/-- **The contract.** If `values` is sorted, `position` is the first place
where `key` can be inserted keeping it sorted: every value before `position` is
smaller than `key`, and every value from `position` on is at least `key`. With
duplicates of `key`, that is the position of the first one; with a missing key,
the position of the first larger value, or the length of the list. -/
def Contract (position : Int) (values : List Int) (key : Int) : Prop :=
  values.Pairwise (· ≤ ·) →
    0 ≤ position ∧ position ≤ Int.ofNat values.length ∧
    (∀ value ∈ values.take position.toNat, value < key) ∧
    (∀ value ∈ values.drop position.toNat, key ≤ value)

end Lookup
