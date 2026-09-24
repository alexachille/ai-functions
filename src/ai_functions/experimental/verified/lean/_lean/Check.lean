/-
The trusted cold checker, run by the support executable in a fresh process.

`compile` elaborates an artifact as a module, confining the model's ranges
(`Confine.lean`), and writes its `.olean`, and its C code if requested, only if
elaboration succeeded. This runs the agent's Lean and is untrusted.

`check` loads a compiled module without elaborating it or running its code,
and accepts it iff:
- it imports exactly `Init` and the expected imports;
- every declaration replays through the kernel;
- the harness's expectations elaborate over the imports, without the module,
  so nothing the agent wrote can change how they read;
- every declaration the expectations add has a twin of the same name in the
  module: of the same kind with a kernel-defeq type, and a defeq value for a
  `def`. An `axiom` twin fixes only the type;
- with a native audit, the module passes the native export policy.
The requested axiom inventories are collected here, not read from the module.
-/
import Lean
import Confine

open Lean Elab

namespace AIFunctionsCheck

def nameOf (parts : Array String) : Name :=
  parts.foldl Name.mkStr .anonymous

structure Audit where
  answer : Array String
  proof : Array String
  trusted_modules : Array String
  fixed_declarations : Array (Array String)
  deriving FromJson

structure Spec where
  module : Array String
  imports : Array (Array String)
  expected : String
  axioms : Array (Array String)
  native_audit : Option Audit
  deriving FromJson

structure CompileRequest where
  module : Array String
  source : String
  olean : String
  /-- Where to write the module's C code, if anywhere. -/
  c : Option String
  model : AIFunctionsConfine.Ranges
  deriving FromJson

def options : Options :=
  ({} : Options).setBool `Elab.async false

/-- Look a constant up in the kernel environment: replayed constants and imported ones. -/
def findConst (env : Environment) (name : Name) : Option ConstantInfo :=
  env.toKernelEnv.find? name

def runCore (env : Environment) (x : CoreM α) : IO α := do
  let ctx : Core.Context := { fileName := "<check>", fileMap := default, options := {} }
  return (← x.toIO ctx { env }).1

/-- The module as read from its object files: its imports, and its own
constants and environment extension entries from the most private part. -/
structure ModuleContents where
  imports : Array Import
  constants : Std.HashMap Name ConstantInfo
  names : Array Name
  entries : Array (Name × Array EnvExtensionEntry)

unsafe def readModule (module : Name) : IO ModuleContents := do
  let mFile ← findOLean module
  unless (← mFile.pathExists) do
    throw <| IO.userError s!"object file '{mFile}' of module {module} does not exist"
  let mut fnames := #[mFile]
  let sFile := OLeanLevel.server.adjustFileName mFile
  if (← sFile.pathExists) then
    fnames := fnames.push sFile
    let pFile := OLeanLevel.private.adjustFileName mFile
    if (← pFile.pathExists) then fnames := fnames.push pFile
  let parts ← readModuleDataParts fnames
  if h : parts.size = 0 then throw <| IO.userError "failed to read module data" else
  let data := parts[parts.size-1].1
  let constants := (data.constNames.zip data.constants).foldl (fun m (n, c) => m.insert n c) {}
  return { imports := parts[0].1.imports, constants, names := data.constNames, entries := data.entries }

/-- Elaborate the expectations over the imports, under the module's name so
private names agree. -/
def elaborate (imported : Environment) (module : Name) (source : String) : IO (Except String Environment) := do
  let state := Command.mkState (imported.setMainModule module) {} options
  let (_, front) ← IO.FS.withIsolatedStreams (isolateStderr := true) <|
    Elab.IO.processCommands (Parser.mkInputContext source "<expected>") {} state
  let failures := front.commandState.messages.toArray.filter (·.severity matches .error)
  if failures.isEmpty then return .ok front.commandState.env
  let texts ← failures.mapM fun m => return s!"{m.pos.line}:{m.pos.column}: {← m.data.toString}"
  return .error s!"The harness's expectations do not elaborate:\n{"\n".intercalate texts.toList}"

def kindOf : ConstantInfo → String
  | .defnInfo _ => "def"
  | .thmInfo _ => "theorem"
  | .axiomInfo _ => "axiom"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quot"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "ctor"
  | .recInfo _ => "recursor"

def value? : ConstantInfo → Option Expr
  | .defnInfo info => some info.value
  | .opaqueInfo info => some info.value
  | _ => none

/-- Compare the module's declaration with its expected twin. -/
def compare (env : Environment) (twin : ConstantInfo) : Except String Unit := do
  let name := twin.name
  let defEq a b := Kernel.isDefEqGuarded env {} a b
  let some info := findConst env name | throw s!"{name} is missing"
  unless twin matches .axiomInfo _ || kindOf info == kindOf twin do
    throw s!"{name} is a {kindOf info}, expected a {kindOf twin}"
  unless info.levelParams == twin.levelParams do
    throw s!"{name} does not have the universe parameters the harness intended"
  unless defEq info.type twin.type do throw s!"{name} does not have the type the harness intended"
  if let some expected := value? twin then
    unless (value? info).any (defEq · expected) do throw s!"{name} does not have the value the harness intended"

/-- Compare every declaration the expectations add with the module's. -/
def checkTwins (env expected : Environment) : Array String :=
  expected.toKernelEnv.constants.foldStage2 (s := #[]) fun (errors : Array String) _ twin =>
    match compare env twin with
    | .ok () => errors
    | .error e => errors.push e

/-- An extension's entries in the module's own object file. -/
unsafe def moduleEntries (ext : PersistentEnvExtension α β σ) (contents : ModuleContents) : Array α :=
  match contents.entries.find? (·.1 == ext.name) with
  | some (_, entries) => unsafeCast entries
  | none => #[]

/-- The replayed environment with the module's own entries of the compiler
attributes the native audit reads (`init`, `builtin_init`, `export`, `extern`,
`implemented_by`) as local entries, so Lean's own lookups see them. Only these
extensions change, and no extension import hook runs. -/
unsafe def withCompilerAttributes (env : Environment) (contents : ModuleContents) : Environment := Id.run do
  let mut env := env
  for (name, value) in moduleEntries regularInitAttr.ext contents do
    env := regularInitAttr.ext.addEntry env (name, value)
  for (name, value) in moduleEntries builtinInitAttr.ext contents do
    env := builtinInitAttr.ext.addEntry env (name, value)
  for (name, value) in moduleEntries exportAttr.ext contents do
    env := exportAttr.ext.addEntry env (name, value)
  for (name, value) in moduleEntries externAttr.ext contents do
    env := externAttr.ext.addEntry env (name, value)
  for (name, value) in moduleEntries Compiler.implementedByAttr.ext contents do
    env := Compiler.implementedByAttr.ext.addEntry env (name, value)
  return env

/-- Look a constant up for the native audit. `Environment.replay` skips unsafe
and partial constants, such as the `_unsafe_rec` runtime counterpart Lean
generates for a total recursive definition, so those come from the module's
own object file; every other constant comes from the kernel environment. -/
def findAuditConst (env : Environment) (contents : ModuleContents) (name : Name) : Option ConstantInfo :=
  match findConst env name with
  | some info => some info
  | none => contents.constants[name]?.filter fun info => info.isUnsafe || info.isPartial
/-- The native export audit, run on the replayed environment, so the module
is loaded once. Its declarations are the ones `leanchecker` replayed, and are
local there; their compiler attributes and `csimp` entries are read from the
module's own object file, so no extension import hook runs. -/
unsafe def nativeAudit (env : Environment) (module : Name) (contents : ModuleContents) (audit : Audit) :
    CoreM (Array String) := do
  let (answer, proof, trusted) := (nameOf audit.answer, nameOf audit.proof, audit.trusted_modules)
  let fixed := audit.fixed_declarations.map nameOf
  let allowed := #[``propext, ``Quot.sound, ``Classical.choice]
  let mut initializers : Array String := #[]
  for imp in contents.imports do
    unless trusted.contains imp.module.toString do
      let some idx := env.getModuleIdx? imp.module | throwError s!"Missing imported module: {imp.module}"
      initializers := initializers.push <|
        mkModuleInitializationFunctionName imp.module (env.getModulePackageByIdx? idx)
  -- `csimp` entries can replace an imported constant; collect them from every
  -- module's entries, the module's own included.
  let mut csimp : NameMap Name := {}
  let mut csimpTheorems : NameSet := {}
  let imported := (List.range env.header.moduleData.size).toArray.map fun idx =>
    Compiler.CSimp.ext.ext.getModuleEntries env (idx : ModuleIdx)
  for entries in imported.push (moduleEntries Compiler.CSimp.ext.ext contents) do
    for entry in entries do
      let e := match entry with | .global e => e | .scoped _ e => e
      csimp := csimp.insert e.fromDeclName e.thmName
      csimpTheorems := csimpTheorems.insert e.thmName
  let mut pending := [answer]
  -- Include unused helpers: a compiler attribute can affect code generation
  -- even when the theorem does not mention its declaration.
  for name in contents.names do
    let some info := findAuditConst env contents name | throwError s!"Missing declaration: {name}"
    -- A prelude declaration never covers the harness roots, where agent code lives.
    let isFixed := ![`A, `H, `J].contains (privateToUserName info.name).getRoot &&
      fixed.any (·.isPrefixOf info.name)
    if !isFixed && !(info matches .thmInfo _) then pending := info.name :: pending
    if (getInitFnNameFor? env info.name).isSome || isIOUnitInitFn env info.name then
      throwError s!"Native artifact contains a module initializer: {info.name}"
    if let some exportName := getExportNameFor? env info.name then
      unless privateToUserName info.name == module ++ `nativeEntry && exportName == `ai_verified_entry do
        throwError s!"Native artifact contains an unowned native export: {info.name}"
    if info.isUnsafe then
      throwError s!"Native artifact contains an unsafe or partial declaration: {info.name}"
    if (Compiler.isUnsafeRecName? info.name).isSome && !info.isPartial then
      throwError s!"Native artifact contains a source-authored reserved runtime helper: {info.name}"
    if info.isPartial then
      -- Lean itself lowers total recursive definitions to a partial runtime
      -- counterpart. Require the kernel-checked, safe definition it implements;
      -- user-authored partial definitions instead have an opaque parent.
      let some safeName := Compiler.isUnsafeRecName? info.name
        | throwError s!"Native artifact contains a partial declaration: {info.name}"
      let some (.defnInfo safe) := findAuditConst env contents safeName
        | throwError s!"Partial runtime declaration has no total definition: {info.name}"
      unless safe.safety == .safe do
        throwError s!"Partial runtime declaration has no safe definition: {info.name}"
    if isExtern env info.name || (Compiler.getImplementedBy? env info.name).isSome ||
        csimpTheorems.contains info.name then
      throwError s!"Native artifact contains a compiler substitution: {info.name}"
    -- Fixed project declarations may contain unused specification axioms.
    -- The exact answer and proof are still audited transitively below, and
    -- executable references to fixed definitions are walked normally.
    unless isFixed do
      -- `collectAxioms` sees nothing of a constant outside the kernel
      -- environment, so for one the replay skipped, collect over what it uses.
      let mut axioms := #[]
      if (findConst env info.name).isSome then axioms ← collectAxioms info.name
      else for c in info.getUsedConstantsAsSet do axioms := axioms ++ (← collectAxioms c)
      for ax in axioms do
        unless allowed.contains ax do
          throwError s!"Native artifact contains an unauthorized assumption: {ax}"
  for name in #[answer, proof] do
    for ax in (← collectAxioms name) do
      unless allowed.contains ax do
        throwError s!"Native artifact contains an unauthorized assumption: {ax}"
  -- Inspect resolved executable dependencies, not spelling in model source.
  -- Imported runtime primitives and their compiler implementations remain
  -- trusted. Proof bodies are erased and are checked by the axiom audit above.
  let forbidden := #[`dbgTrace, `dbgTraceIfShared, `dbgStackTrace, `dbgSleep, `panic, `panicCore,
    `Float.toBits, `System.Platform.isWindows, `System.Platform.isOSX,
    `System.Platform.isEmscripten]
  let mut seen : NameSet := {}
  while !pending.isEmpty do
    let name := pending.head!
    pending := pending.tail!
    if seen.contains name then continue
    seen := seen.insert name
    if forbidden.contains name then
      throwError s!"Native implementation depends on a runtime effect or unsupported primitive: {name}"
    let some info := findAuditConst env contents name | throwError s!"Missing native dependency: {name}"
    if info matches .thmInfo _ then continue
    if let some idx := env.getModuleIdxFor? name then
      let moduleName := env.header.moduleNames[idx.toNat]!
      unless moduleName == module || trusted.contains moduleName.toString do
        throwError s!"Native implementation depends on imported project code: {name} from {moduleName}. Imported application declarations may be used in proofs; executable dependencies on imported application modules are not supported yet."
    -- Unlike extern/implemented_by attributes, csimp entries can replace an
    -- imported constant. Check the effective rewrite's owner as well as the
    -- declaration being compiled.
    if let some replacement := csimp.find? name then
      let some idx := env.getModuleIdxFor? replacement
        | throwError s!"Native artifact changes a compiler simplification: {name}"
      let owner := env.header.moduleNames[idx.toNat]!
      unless trusted.contains owner.toString do
        throwError (if owner == module then s!"Native artifact changes a compiler simplification: {name}"
          else s!"Project changes a native compiler simplification: {name}")
    if isExtern env name || (Compiler.getImplementedBy? env name).isSome then continue
    let runtimeName := Compiler.mkUnsafeRecName name
    if (findAuditConst env contents runtimeName).isSome then pending := runtimeName :: pending
    if let some value := info.value? then
      pending := value.getUsedConstants.toList ++ pending
  return initializers

def reply (errors : Array String) (inventories : List (String × Json) := []) (nativeImports : Array String := #[]) :
    Json :=
  Json.mkObj [("ok", toJson errors.isEmpty), ("errors", toJson errors), ("axioms", Json.mkObj inventories),
    ("native_imports", toJson nativeImports)]

unsafe def checkSpec (spec : Spec) : IO Json := do
  let module := nameOf spec.module
  let contents ← readModule module
  -- Lean imports `Init` into every module that is not a `prelude`; a repeated import changes nothing.
  let distinct (names : Array Name) := names.foldl (fun acc n => if acc.contains n then acc else acc.push n) #[]
  let found := distinct (contents.imports.map (·.module))
  let intended := distinct (#[`Init] ++ spec.imports.map nameOf)
  if found != intended then
    return reply #[s!"{module} imports {found.toList}, but the harness expected {intended.toList}"]
  -- One import serves both the replay of the module and the expectations.
  enableInitializersExecution
  let imported ← importModules contents.imports options (leakEnv := true) (loadExts := true)
  let env := Environment.ofKernelEnv (← imported.replay contents.constants).toKernelEnv
  let mut errors := match ← elaborate imported module spec.expected with
    | .error e => #[e]
    | .ok expected => checkTwins env expected
  let mut inventories := []
  for name in spec.axioms.map nameOf do
    if (findConst env name).isNone then
      errors := errors.push s!"Unknown audit declaration: {name}"
    else
      inventories := (toString name, toJson ((← runCore env (collectAxioms name)).map toString)) :: inventories
  let mut nativeImports := #[]
  if let some audit := spec.native_audit then
    if errors.isEmpty then
      let audited := withCompilerAttributes env contents
      try nativeImports ← runCore audited (nativeAudit audited module contents audit)
      catch e => errors := errors.push (toString e)
  return reply errors inventories nativeImports

/-- Check a compiled module; an error while reading or replaying it rejects it. -/
unsafe def check (request : Json) : IO Json := do
  try checkSpec (← IO.ofExcept (fromJson? request)) catch e => return reply #[toString e]

/-- Compile an artifact; the `.olean`, and the C code if requested, are written only if it elaborates. -/
unsafe def compile (request : Json) : IO Json := do
  let request : CompileRequest ← IO.ofExcept (fromJson? request)
  enableInitializersExecution
  let ictx := Parser.mkInputContext request.source "<artifact>"
  let (header, pstate, messages) ← Parser.parseHeader ictx
  let (env, messages) ← Elab.processHeader header options messages ictx (trustLevel := 1024) (leakEnv := true)
    (mainModule := nameOf request.module)
  let (_, result) ← IO.FS.withIsolatedStreams (isolateStderr := true) <|
    AIFunctionsConfine.run ictx request.model pstate (Command.mkState env messages options)
  let failures := result.commandState.messages.toArray.filter (·.severity matches .error)
  let errors ← failures.mapM fun m => return s!"{m.pos.line}:{m.pos.column}: {← m.data.toString}"
  if errors.isEmpty then
    let env := result.commandState.env
    writeModule env request.olean
    if let some c := request.c then
      let context := { fileName := "<artifact>", fileMap := default }
      IO.FS.writeFile c (← (Compiler.LCNF.emitC env.mainModule).toIO' context { env })
  return Json.mkObj [("ok", toJson errors.isEmpty), ("errors", toJson errors)]

end AIFunctionsCheck
