/-! # Data-export policy

May a user export a dataset today? The policy reads:

* The user must be an active employee.
* Public and internal datasets (classification 0 or 1) may then be exported.
* A restricted dataset (classification 2) may be exported only if, in
  addition, the user is not a contractor, the user's data-handling training is
  still valid today, and the user has access through a chain of at most 2
  delegations starting from the dataset's owner, with every grant in the chain
  still in force today.

Days are absolute day numbers. Every fact about the world (the directory, the
grants, owners, classifications, training) is `opaque` below: Lean learns a
value only from a recorded tool call. `grantsFor` returns the *complete* list
of a user's grants, so its record also rules grants out: after rewriting with
it, "every grant of this user" ranges over a concrete list. -/

namespace Access

/-- A grant of access: `(delegatedBy, expiresDay)`, the user who delegated it
and the last day it is in force. -/
abbrev Grant := String × Nat

/-! ## Facts about the world, looked up with tools -/

/-- A user's directory entry: `(role, active)`, the job role and whether the
person is currently employed. -/
opaque employment : (user : String) → String × Bool

/-- Every grant `user` holds on `dataset`. The list is complete: a grant not in
it does not exist. -/
opaque grantsFor : (user : String) → (dataset : String) → List Grant

/-- The owner of a dataset, who has access outright. -/
opaque ownerOf : (dataset : String) → String

/-- A dataset's classification: 0 public, 1 internal, 2 restricted. -/
opaque classification : (dataset : String) → Nat

/-- The last day the user's data-handling training is valid; 0 if never taken. -/
opaque trainingExpiry : (user : String) → Nat

/-! ## The policy -/

/-- The classification level `internal`; anything above it is restricted. -/
def internal : Nat := 1

/-- A grant is in force on `today` if it has not expired yet. -/
def activeOn (grant : Grant) (today : Nat) : Bool := today ≤ grant.2

/-- `HasAccess hops user dataset today`: `user` reaches `dataset` within `hops`
delegations of its owner. Either `user` is the owner, or `user` holds a grant in
force today from someone who has access within `hops - 1` delegations. With 0
hops, only the owner has access. -/
def HasAccess : (hops : Nat) → (user dataset : String) → (today : Nat) → Prop
  | 0, user, dataset, _ => ownerOf dataset = user
  | hops + 1, user, dataset, today => ownerOf dataset = user ∨
      ∃ grant ∈ grantsFor user dataset, activeOn grant today = true ∧ HasAccess hops grant.1 dataset today

/-- The export policy: an active employee may export a public or internal
dataset; a restricted one needs, in addition, a non-contractor role, valid
training, and access within 2 delegations of the owner. -/
def MayExport (user dataset : String) (today : Nat) : Prop :=
  (employment user).2 = true ∧
  (classification dataset ≤ internal ∨
    ((employment user).1 ≠ "contractor" ∧
     today ≤ trainingExpiry user ∧
     HasAccess 2 user dataset today))

/-! ## The contract -/

/-- **The contract.** The decision is `true` exactly when the policy allows the
export: allowing a forbidden export and denying an allowed one are both wrong. -/
def DecisionCorrect (decision : Bool) (user dataset : String) (today : Nat) : Prop :=
  decision = true ↔ MayExport user dataset today

/-! ## Reasoning about delegation chains, one hop at a time

`HasAccess` mentions opaque facts at every level, so nothing computes on its
own: rewrite with the recorded tool facts, and what remains is decidable. -/

/-- The owner has access, within any number of hops. -/
theorem hasAccess_of_owner (hops : Nat) {user dataset : String} (today : Nat)
    (h : ownerOf dataset = user) : HasAccess hops user dataset today := by
  cases hops with
  | zero => exact h
  | succ hops => exact Or.inl h

/-- One more hop: a grant in `grantsFor user dataset`, in force today, from
someone who has access within `hops`, gives `user` access within `hops + 1`. -/
theorem hasAccess_delegated {hops : Nat} {user dataset : String} {today : Nat} {grant : Grant}
    (hgrant : grant ∈ grantsFor user dataset) (hactive : activeOn grant today = true)
    (hfrom : HasAccess hops grant.1 dataset today) : HasAccess (hops + 1) user dataset today :=
  Or.inr ⟨grant, hgrant, hactive, hfrom⟩

/-- With 0 hops, a user who is not the owner has no access. -/
theorem not_hasAccess_zero {user dataset : String} {today : Nat}
    (h : ownerOf dataset ≠ user) : ¬ HasAccess 0 user dataset today := h

/-- No access within `hops + 1`: `user` is not the owner, and every grant of
`user` has expired or comes from someone without access within `hops`. Rewrite
with the recorded `grantsFor user dataset` first, so that "every grant" ranges
over a concrete list. -/
theorem not_hasAccess_succ {hops : Nat} {user dataset : String} {today : Nat}
    (hnotOwner : ownerOf dataset ≠ user)
    (h : ∀ grant ∈ grantsFor user dataset,
      activeOn grant today = false ∨ ¬ HasAccess hops grant.1 dataset today) :
    ¬ HasAccess (hops + 1) user dataset today := by
  intro hacc
  cases hacc with
  | inl howner => exact hnotOwner howner
  | inr hex =>
    obtain ⟨grant, hgrant, hactive, hfrom⟩ := hex
    cases h grant hgrant with
    | inl hexpired => simp [hexpired] at hactive
    | inr hno => exact hno hfrom

/-! ## Two ways to prove the contract -/

/-- To allow, prove that the policy allows the export. -/
theorem allow_of_policy {user dataset : String} {today : Nat}
    (h : MayExport user dataset today) : DecisionCorrect true user dataset today :=
  ⟨fun _ => h, fun _ => rfl⟩

/-- To deny, prove that the policy does not allow the export. -/
theorem deny_of_policy {user dataset : String} {today : Nat}
    (h : ¬ MayExport user dataset today) : DecisionCorrect false user dataset today :=
  ⟨fun hfalse => absurd hfalse (by simp), fun hallowed => absurd hallowed h⟩

end Access
