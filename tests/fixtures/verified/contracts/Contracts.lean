-- Result-first contracts for the verified.ai_compile tests. Each namespace holds a
-- Boolean precondition `pre`, a postcondition `post` on the result, and the
-- `Contract` relating a result to the inputs.
-- Core only: importing `Lean` costs seconds in every Lean process.

namespace Contracts.Clamp
def pre (v0 : Int) (v1 : Int) (v2 : Int) : Bool := (decide (v1 <= v2))
def post (r : Int) (v0 : Int) (v1 : Int) (v2 : Int) : Bool := (((decide (v1 <= r)) && (decide (r <= v2))) && (if (decide (v0 < v1)) then (decide (r = v1)) else (if (decide (v0 > v2)) then (decide (r = v2)) else (decide (r = v0)))))
def Contract (r : Int) (v0 : Int) (v1 : Int) (v2 : Int) : Prop :=
  pre v0 v1 v2 = true → post r v0 v1 v2 = true
end Contracts.Clamp

namespace Contracts.MultiplyAdd
def pre (v0 : Float) (v1 : Float) (v2 : Float) (v3 : Float) : Bool := true
def post (r : Bool) (v0 : Float) (v1 : Float) (v2 : Float) (v3 : Float) : Bool := (decide (r = (Float.beq ((v0 * v1) + v2) v3)))
def Contract (r : Bool) (v0 : Float) (v1 : Float) (v2 : Float) (v3 : Float) : Prop :=
  pre v0 v1 v2 v3 = true → post r v0 v1 v2 v3 = true
end Contracts.MultiplyAdd

namespace Contracts.IntIdentity
def pre (v0 : Int) : Bool := true
def post (r : Int) (v0 : Int) : Bool := (decide (r = v0))
def Contract (r : Int) (v0 : Int) : Prop :=
  pre v0 = true → post r v0 = true
end Contracts.IntIdentity

namespace Contracts.SameLength
def pre (v0 : List Int) : Bool := true
def post (r : List Int) (v0 : List Int) : Bool := (decide ((Int.ofNat (List.length r)) = (Int.ofNat (List.length v0))))
def Contract (r : List Int) (v0 : List Int) : Prop :=
  pre v0 = true → post r v0 = true
end Contracts.SameLength

namespace Contracts.Mixed
def pre (v0 : Int) (v1 : Bool) (v2 : Float) (v3 : List Int) : Bool := true
def post (r : Bool) (v0 : Int) (v1 : Bool) (v2 : Float) (v3 : List Int) : Bool := (decide (r = ((v1 && (decide (v0 = (Int.ofNat (List.length v3))))) && (Float.le (Float.ofBits (0x0000000000000000 : UInt64)) v2))))
def Contract (r : Bool) (v0 : Int) (v1 : Bool) (v2 : Float) (v3 : List Int) : Prop :=
  pre v0 v1 v2 v3 = true → post r v0 v1 v2 v3 = true
end Contracts.Mixed

namespace Contracts.Ninth
def pre (v0 : Int) (v1 : Int) (v2 : Int) (v3 : Int) (v4 : Int) (v5 : Int) (v6 : Int) (v7 : Int) (v8 : Int) : Bool := true
def post (r : Int) (v0 : Int) (v1 : Int) (v2 : Int) (v3 : Int) (v4 : Int) (v5 : Int) (v6 : Int) (v7 : Int) (v8 : Int) : Bool := (decide (r = v8))
def Contract (r : Int) (v0 : Int) (v1 : Int) (v2 : Int) (v3 : Int) (v4 : Int) (v5 : Int) (v6 : Int) (v7 : Int) (v8 : Int) : Prop :=
  pre v0 v1 v2 v3 v4 v5 v6 v7 v8 = true → post r v0 v1 v2 v3 v4 v5 v6 v7 v8 = true
end Contracts.Ninth

namespace Contracts.FloatClass
def pre (v0 : Float) : Bool := true
def post (r : Float) (v0 : Float) : Bool := ((decide ((Float.isNaN r) = (Float.isNaN v0))) && (decide ((Float.isFinite r) = (Float.isFinite v0))))
def Contract (r : Float) (v0 : Float) : Prop :=
  pre v0 = true → post r v0 = true
end Contracts.FloatClass
