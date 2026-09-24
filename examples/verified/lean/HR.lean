/-! # Leave requests

When a leave request is approved, and the employee records that decides it. -/

namespace HR

/-! ## Employee records, looked up with tools -/

/-- How long the employee has worked here, in whole months. -/
opaque tenureMonths : (employee : String) → Nat

/-- Vacation days the employee has left this year. -/
opaque vacationDaysLeft : (employee : String) → Nat

/-- Sick days the employee has already taken this year. -/
opaque sickDaysTaken : (employee : String) → Nat

/-! ## The policy -/

/-- A leave of some kind, lasting some number of working days. -/
inductive Leave where
  | vacation (days : Nat)
  | sick (days : Nat)
  deriving Inhabited

/-- A request is approved when:
* vacation: the employee has worked here at least 6 months and has the days left;
* sick leave: the year's sick days stay within 10. -/
def isApproved (employee : String) : Leave → Prop
  | .vacation days => 6 ≤ tenureMonths employee ∧ days ≤ vacationDaysLeft employee
  | .sick days => sickDaysTaken employee + days ≤ 10

/-! ## The request and the contract -/

/-- The leave the message asks for: its kind and number of working days.
No one can look this up: the AI reads it from the message, and a human can
review that reading. -/
opaque requestedLeave : (message : String) → Leave

/-- **Contract.** The decision is `true` exactly when the requested leave is approved. -/
def DecisionCorrect (approved : Bool) (employee message : String) : Prop :=
  approved = true ↔ isApproved employee (requestedLeave message)

end HR
