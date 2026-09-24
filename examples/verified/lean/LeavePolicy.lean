/-! # Leave policy

The rules of the company's leave policy, and the employee records they use.
The policy defines when leave is approved and when it is paid. It does not
define answers to particular questions, such as how many days an employee has
left: an answer is derived from these rules. -/

namespace LeavePolicy

/-! ## Employee records, looked up with tools -/

/-- How long the employee has worked here, in whole months. -/
opaque tenureMonths : (employee : String) → Nat

/-- Whole months the employee has worked so far this year. -/
opaque monthsWorkedThisYear : (employee : String) → Nat

/-- Vacation days the employee has already taken this year. -/
opaque vacationDaysTaken : (employee : String) → Nat

/-- Sick days the employee has already taken this year. -/
opaque sickDaysTaken : (employee : String) → Nat

/-! ## The rules -/

/-- A leave of some kind, lasting some number of working days. -/
inductive Leave where
  | vacation (days : Nat)
  | sick (days : Nat)
  | unpaid (days : Nat)

/-- Vacation days earned by working some months of the year: one and a half per month. -/
def earnedDays (months : Nat) : Nat := months * 3 / 2

/-- The vacation days in a list of leaves. -/
def vacationDays : List Leave → Nat
  | [] => 0
  | .vacation days :: rest => days + vacationDays rest
  | _ :: rest => vacationDays rest

/-- Whether the employee may take this kind of leave at all, some months from now:
vacation after 6 months here, sick leave always, unpaid leave of at most 20 days
after a year here. -/
def isEligible (employee : String) (monthsFromNow : Nat) : Leave → Prop
  | .vacation _ => 6 ≤ tenureMonths employee + monthsFromNow
  | .sick _ => True
  | .unpaid days => 12 ≤ tenureMonths employee + monthsFromNow ∧ days ≤ 20

/-- Requests made together, some months from now (0 is today), are approved when
the employee is eligible for each, and the vacation already taken plus the
vacation requested fits what they will have earned this year by then. -/
def areApproved (employee : String) (monthsFromNow : Nat) (requests : List Leave) : Prop :=
  (∀ leave ∈ requests, isEligible employee monthsFromNow leave) ∧
    vacationDaysTaken employee + vacationDays requests ≤
      earnedDays (min 12 (monthsWorkedThisYear employee + monthsFromNow))

/-- A single request made today is approved. -/
def isLeaveApproved (employee : String) (leave : Leave) : Prop :=
  areApproved employee 0 [leave]

/-- The employee is paid during the leave: vacation is paid, sick leave is paid
up to 10 sick days in the year, unpaid leave is not. -/
def isPaidLeave (employee : String) : Leave → Prop
  | .vacation _ => True
  | .sick days => sickDaysTaken employee + days ≤ 10
  | .unpaid _ => False

/-! ## The question and the contract -/

/-- The yes/no question the employee's message asks, stated with this policy.
No one can look this up: the AI reads the message and records its reading as a
judgment, which a human reviews. -/
opaque asks : (employee message : String) → Prop

/-- **Contract.** The answer is `true` exactly when what the message asks holds. -/
def Answered (answer : Bool) (employee message : String) : Prop :=
  answer = true ↔ asks employee message

end LeavePolicy
