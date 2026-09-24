/-! # Settlement-ledger audit

A merchant's settlement ledger lists transactions, page by page. The audit
must return the ids of the transactions whose recorded fee is wrong, flagging
exactly those, and show that the account balance never goes negative.

A row is `(id, kind, amountCents, feeCents)`. Kind `0` is a payout: money
leaves the account, `amount + fee`. Any other kind is a sale, credited net of
its fee, `amount - fee`. The fee schedule gives a rate in basis points
(hundredths of a percent) per kind; a recorded fee is correct when it is
exactly `⌊amount * rate / 10000⌋`.

Nothing below computes on its own. The ledger and the fee schedule are
`opaque`, known only from recorded tool calls; `FeeOk` says a remainder
*exists*, with no procedure to decide it; `Solvent` speaks about every prefix
of the ledger. To prove the contract, write the checking programs (a per-row
fee check, a running-balance scan), prove them correct against these
definitions, and run them on the fetched pages. -/

namespace Audit

/-- A ledger row: `(id, kind, amountCents, feeCents)`. -/
abbrev Tx := Nat × Nat × Nat × Nat

/-- The transaction id of a row. -/
def txId (t : Tx) : Nat := t.1

/-- The kind of a row: `0` is a payout, anything else a sale. -/
def txKind (t : Tx) : Nat := t.2.1

/-- The gross amount of a row, in cents. -/
def txAmount (t : Tx) : Nat := t.2.2.1

/-- The fee recorded on a row, in cents. -/
def txFee (t : Tx) : Nat := t.2.2.2

/-! ## Facts about the world, looked up with tools -/

/-- How many pages the ledger has. -/
opaque numPages : Nat

/-- Page `index` of the ledger, for `index < numPages`: all of its rows, in
ledger order. A row not on any page does not exist. -/
opaque page : (index : Nat) → List Tx

/-- The fee rate for a transaction kind, in basis points. -/
opaque feeBps : (kind : Nat) → Nat

/-! ## The ledger as one list -/

/-- `count` pages starting at page `first`, concatenated in order. -/
def pagesFrom : (first count : Nat) → List Tx
  | _, 0 => []
  | first, count + 1 => page first ++ pagesFrom (first + 1) count

/-- The whole ledger: pages `0 … numPages - 1`, in order. Unfold it with the
recorded `numPages` and the two `pagesFrom` equations below. -/
def table : List Tx := pagesFrom 0 numPages

theorem pagesFrom_zero (first : Nat) : pagesFrom first 0 = [] := rfl

theorem pagesFrom_succ (first count : Nat) :
    pagesFrom first (count + 1) = page first ++ pagesFrom (first + 1) count := rfl

/-! ## The rules -/

/-- How a row changes the account balance, in cents: a payout debits
`amount + fee`, a sale credits `amount - fee`. -/
def net (t : Tx) : Int :=
  if txKind t = 0 then -((txAmount t : Int) + txFee t)
  else (txAmount t : Int) - txFee t

/-- The recorded fee is exactly `⌊amount * feeBps kind / 10000⌋`, stated as a
division with remainder. A specification, not an algorithm: decide it with a
`Bool` check proved equivalent under the recorded `feeBps`. -/
def FeeOk (t : Tx) : Prop :=
  ∃ remainder, txAmount t * feeBps (txKind t) = 10000 * txFee t + remainder ∧ remainder < 10000

/-- The balance never goes negative: the net sum of every prefix of the ledger
is at least zero. (`take` stops at the end of the list, so `∀ n` covers exactly
the prefixes.) -/
def Solvent : Prop :=
  ∀ n, 0 ≤ ((table.take n).map net).sum

/-! ## The contract -/

/-- **The contract.** Every flagged id belongs to a row with a wrong fee (no
false alarms), every row with a wrong fee is flagged (nothing escapes), and the
ledger is solvent. -/
def AuditCorrect (flags : List Nat) : Prop :=
  (∀ i ∈ flags, ∃ t ∈ table, txId t = i ∧ ¬ FeeOk t) ∧
  (∀ t ∈ table, ¬ FeeOk t → txId t ∈ flags) ∧
  Solvent

end Audit
