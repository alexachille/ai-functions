-- Core only: importing `Lean` costs seconds in every Lean process.
namespace Fixture

def Contract (result : Int) (base : Int) (enabled : Bool) (xs : List Int) : Prop :=
  result = if enabled then base + xs.foldl (· + ·) 0 else base

end Fixture
