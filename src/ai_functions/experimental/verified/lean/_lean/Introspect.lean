/- Symbol introspection for preparation and `describe()`, compiled into the session server. -/
import Lean

open Lean Meta

def aiFunctionsModule (env : Environment) (name : Name) : Name :=
  match env.getModuleIdxFor? name with
  | some idx => env.header.moduleNames[idx.toNat]!
  | none => env.header.mainModule

-- Follow declaration references within the application's own modules. Import
-- availability does not determine how much source belongs in a model prompt.
partial def aiFunctionsContext (env : Environment) (owned : Array Name) (name : Name) :
    Array String := Id.run do
  let mut pending := [name]
  let mut visited : NameSet := {}
  let mut modules : Array String := #[]
  while !pending.isEmpty do
    let current := pending.head!
    pending := pending.tail!
    unless visited.contains current do
      visited := visited.insert current
      let moduleName := aiFunctionsModule env current
      if owned.contains moduleName then
        unless modules.contains moduleName.toString do
          modules := modules.push moduleName.toString
        if let some info := env.find? current then
          let refs := info.type.getUsedConstants ++
            (info.value?.map Expr.getUsedConstants).getD #[]
          pending := refs.toList ++ pending
  return modules

partial def aiFunctionsType (e : Expr) : MetaM Json := do
  let e ← whnf e
  match e with
  | .forallE _ d b bi =>
    if b.hasLooseBVars || !bi.isExplicit then return Json.null
    return Json.mkObj [("k", "arrow"), ("d", ← aiFunctionsType d), ("c", ← aiFunctionsType b)]
  | .sort u =>
    return if u matches .zero then Json.mkObj [("k", "const"), ("n", "Prop")] else Json.null
  | _ =>
    match e.getAppFn with
    | .const n levels =>
      -- Preserve the monomorphic Unit alias at the Python boundary. PUnit in
      -- other universes is not the native zero-argument calling convention.
      if n == ``PUnit then
        return if levels == [Level.succ Level.zero] then
          Json.mkObj [("k", "const"), ("n", "Unit")]
        else Json.null
      let args := e.getAppArgs
      if n == `Prod && args.size == 2 then
        return Json.mkObj [("k", "prod"), ("l", ← aiFunctionsType args[0]!), ("r", ← aiFunctionsType args[1]!)]
      let js ← args.mapM aiFunctionsType
      if js.any (· == Json.null) then return Json.null
      return Json.mkObj [("k", "const"), ("n", toString n), ("a", Json.arr js)]
    | _ => return Json.null

/-- Lean's metadata for one declaration, in the form `LeanSymbol.signature` reads. -/
def aiFunctionsDescribe (env : Environment) (owned : Array Name) (spelling : String) (n : Name) :
    MetaM (Option Json) := do
    let some info := env.find? n | return none
    let kind := match info with
      | .axiomInfo _ => "axiom"
      | .defnInfo _ => "def"
      | .thmInfo _ => "theorem"
      | .opaqueInfo _ => "opaque"
      | .quotInfo _ => "quot"
      | .inductInfo _ => "inductive"
      | .ctorInfo _ => "ctor"
      | .recInfo _ => "recursor"
    let typeString := (← ppExpr info.type).pretty
    let typeJson ← aiFunctionsType info.type
    forallTelescope info.type fun xs result => do
      let mut params := #[]
      let mut simple := info.levelParams.isEmpty
      for x in xs do
        let decl ← x.fvarId!.getDecl
        let ty ← aiFunctionsType decl.type
        if !decl.binderInfo.isExplicit || decl.type.hasFVar then simple := false
        params := params.push <| Json.mkObj [
          ("name", toString decl.userName), ("type", ty),
          ("type_str", (← ppExpr decl.type).pretty),
          ("explicit", toJson decl.binderInfo.isExplicit)]
      let prop := result.isProp
      if result.hasFVar then simple := false
      let moduleName := (aiFunctionsModule env n).toString
      return some <| Json.mkObj [
        ("name", spelling), ("kind", kind), ("module", moduleName),
        ("source_modules", toJson <| aiFunctionsContext env owned n),
        ("type_str", typeString), ("type", typeJson), ("params", Json.arr params),
        ("result", ← aiFunctionsType result), ("result_str", (← ppExpr result).pretty),
        ("prop", toJson prop), ("simple", toJson simple)]
