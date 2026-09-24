/-
Confinement of model-authored Lean.

`run` elaborates an input command by command, as Lean's frontend does. The
harness marks the byte ranges the model wrote. A command overlapping them is
checked before it elaborates (its kind, if it starts in model text; every
syntax node starting in model text) and after (every constant it added). The
first violation is logged as an error and nothing after it runs. The session
server and the checker's compile step both elaborate through `run`.
-/
import Lean

open Lean Elab

namespace AIFunctionsConfine

/-- Byte ranges `[start, stop)` of model-authored text. -/
abbrev Ranges := Array (Nat × Nat)

def inModel (ranges : Ranges) (pos : String.Pos.Raw) : Bool :=
  ranges.any fun (start, stop) => start ≤ pos.byteIdx && pos.byteIdx < stop

structure Violation where
  pos : String.Pos.Raw
  text : String

def rejected (what : String) : String :=
  s!"{what} is not permitted in model-authored Lean"

def declarationKinds : Array SyntaxNodeKind :=
  #[``Parser.Command.definition, ``Parser.Command.theorem, ``Parser.Command.abbrev,
    ``Parser.Command.example, ``Parser.Command.structure, ``Parser.Command.inductive]

def commandKinds : Array SyntaxNodeKind :=
  #[``Parser.Command.declaration, ``Parser.Command.open, ``Parser.Command.in,
    ``Parser.Command.variable, ``Parser.Command.universe, ``Parser.Command.moduleDoc,
    ``Parser.Command.eoi]

def allowedCommands : String :=
  "def, theorem, abbrev, example, structure, inductive, open, variable, universe, and `… in …` of these"

def attributes : Array Name := #[`simp, `reducible, `irreducible, `inline]

def bannedKinds : Array (SyntaxNodeKind × String) :=
  #[(``Parser.Command.unsafe, "An `unsafe` declaration"),
    (``Parser.Command.partial, "A `partial` definition"),
    (``Parser.Command.meta, "A `meta` definition"),
    (``Parser.Termination.partialFixpoint, "`partial_fixpoint`"),
    (``Parser.Term.unsafe, "An `unsafe` term"),
    (`Lean.byElab, "`by_elab`"),
    (`Lean.Parser.Tactic.runTac, "`run_tac`"),
    (`Lean.includeStr, "`include_str`"),
    (``Parser.Term.sorry, "`sorry`"),
    (`Lean.Parser.Tactic.tacticSorry, "`sorry`"),
    (`Lean.Parser.Tactic.tacticAdmit, "`admit`"),
    (`Lean.Parser.Tactic.nativeDecide, "`native_decide`"),
    (``Parser.Term.set_option, "`set_option`"),
    (`Lean.Parser.Tactic.set_option, "`set_option`")]

/-- The first token of a syntax tree, to name a command as it was written. -/
partial def firstAtom : Syntax → Option String
  | .atom _ val => some val
  | .ident _ raw _ _ => some raw.toString
  | .node _ _ args => args.findSome? firstAtom
  | .missing => none

/-- The kind check of a command that starts in model text. -/
partial def commandViolation (cmd : Syntax) : Option Violation :=
  let pos := cmd.getPos?.getD 0
  let refuse (stx : Syntax) :=
    some ⟨stx.getPos?.getD pos, rejected s!"The command `{(firstAtom stx).getD "?"}` ({stx.getKind}). Allowed commands: {allowedCommands}"⟩
  if cmd.getKind == ``Parser.Command.in then commandViolation cmd[0] <|> commandViolation cmd[2]
  else if cmd.getKind == ``Parser.Command.declaration then
    if declarationKinds.contains cmd[1].getKind then none else refuse cmd[1]
  else if commandKinds.contains cmd.getKind then none
  else refuse cmd

/-- Namespaces the syntax opens, from command, term and tactic `open`s. Every
identifier is tried, so this over-approximates what the command sees. -/
partial def openedNamespaces (env : Environment) (scope : Command.Scope) (stx : Syntax)
    (acc : List OpenDecl) : List OpenDecl :=
  match stx with
  | .node _ kind args =>
    let acc := if kind ∈ [``Parser.Command.openSimple, ``Parser.Command.openScoped,
        ``Parser.Command.openOnly, ``Parser.Command.openHiding, ``Parser.Command.openRenaming] then
      args.foldl (init := acc) fun acc arg =>
        (arg.getArgs.push arg).foldl (init := acc) fun acc id =>
          if id.isIdent then
            (ResolveName.resolveNamespace env scope.currNamespace scope.openDecls id.getId).foldl
              (fun acc ns => .simple ns [] :: acc) acc
          else acc
    else acc
    args.foldl (init := acc) fun acc arg => openedNamespaces env scope arg acc
  | _ => acc

/-- The attribute's name, as Lean's `elabAttr` reads it. -/
def attributeName (attr : Syntax) : Name :=
  if attr.getKind == ``Parser.Attr.simple then attr[0].getId.eraseMacroScopes
  else match attr.getKind with
    | .str _ s => Name.mkSimple s
    | kind => kind

/-- Constants model text may not name: they run compiled code in the kernel,
or assume anything. -/
def deniedConstants : Array Name :=
  #[``Lean.reduceBool, ``Lean.reduceNat, ``Lean.ofReduceBool, ``Lean.ofReduceNat, ``Lean.trustCompiler, ``sorryAx]

/-- Whether model text may not reference `n`. -/
def denied (env : Environment) (n : Name) : Bool :=
  deniedConstants.contains n || (env.find? n).any (·.isUnsafe)

/-- The check of one syntax node that starts in model text, not its children. -/
def ownViolation (env : Environment) (scope : Command.Scope) (opens : List OpenDecl) :
    Syntax → Option String
  | stx@(.node _ kind _) =>
    if let some (_, what) := bannedKinds.find? (·.1 == kind) then some (rejected what)
    else if kind == ``Parser.Term.attrInstance && !attributes.contains (attributeName stx[1]) then
      some (rejected s!"The attribute `{attributeName stx[1]}` (allowed: {", ".intercalate (attributes.toList.map toString)})")
    else if kind == ``Parser.Command.declId && stx[0].getId.getRoot == `_root_ then
      some (rejected "A `_root_` declaration name")
    else none
  | .ident _ _ id _ =>
    (ResolveName.resolveGlobalName env scope.opts scope.currNamespace opens id).findSome? fun (n, _) =>
      if denied env n then some (rejected s!"The constant `{n}`") else none
  | _ => none

/-- The check of every node that starts in model text. -/
partial def nodeViolation (env : Environment) (scope : Command.Scope) (opens : List OpenDecl)
    (ranges : Ranges) (stx : Syntax) : Option Violation :=
  let own := stx.getPos?.filter (inModel ranges) |>.bind fun pos =>
    (ownViolation env scope opens stx).map (⟨pos, ·⟩)
  own <|> stx.getArgs.findSome? (nodeViolation env scope opens ranges)

/-- The check of a command before it elaborates. -/
def preCheck (env : Environment) (scope : Command.Scope) (ranges : Ranges) (cmd : Syntax) :
    Option Violation :=
  (if cmd.getPos?.any (inModel ranges) then commandViolation cmd else none) <|>
    nodeViolation env scope (openedNamespaces env scope cmd scope.openDecls) ranges cmd

def hasNativeComponent : Name → Bool
  | .str p s => s == "_native" || hasNativeComponent p
  | .num p _ => hasNativeComponent p
  | .anonymous => false

/-- The check of the constants a command with model text added. -/
def postCheck (before : NameSet) (env : Environment) : BaseIO (Option String) := do
  let infos ← env.getLocalConstantInfos
  for async in infos do
    let info := async.toConstantInfo
    let name := info.name
    if before.contains name then continue
    if hasNativeComponent name then return some (rejected "`native_decide`")
    -- The compiler's runtime counterpart of a safe recursive definition.
    if (Compiler.isUnsafeRecName? name).any fun safe => (env.find? safe).any (!·.isUnsafe) then continue
    if info.isUnsafe then return some (rejected s!"The unsafe declaration `{name}`")
    let used := info.type.getUsedConstants ++ (info.value?.map (·.getUsedConstants)).getD #[]
    -- Failed elaboration leaves `sorryAx` behind; Lean reports that error itself.
    if let some bad := used.find? fun n => n != ``sorryAx && denied env n then
      return some (rejected s!"A reference to the constant `{bad}` (in `{name}`)")
  return none

structure Result where
  commandState : Command.State
  /-- The confinement violation that stopped elaboration. -/
  rejected : Option String := none

/-- Elaborate one command, keeping the earlier messages. -/
def elabCommand (ictx : Parser.InputContext) (cmdPos : String.Pos.Raw) (cmd : Syntax)
    (st : Command.State) : IO Command.State := do
  let ctx : Command.Context :=
    { cmdPos, fileName := ictx.fileName, fileMap := ictx.fileMap, snap? := none, cancelTk? := none }
  match ← EIO.toIO' ((Command.elabCommandTopLevel cmd #[] ctx).run st) with
  | .error e => throw <| IO.userError s!"unexpected internal error: {← e.toMessageData.toString}"
  | .ok ((), after) => return { after with messages := st.messages ++ after.messages }

/-- Elaborate the input from `pstate` on, confining the model's ranges. -/
partial def run (ictx : Parser.InputContext) (ranges : Ranges) (pstate : Parser.ModuleParserState)
    (st : Command.State) : IO Result := do
  let scope := st.scopes.head!
  let pmctx : Parser.ParserModuleContext :=
    { env := st.env, options := scope.opts, currNamespace := scope.currNamespace, openDecls := scope.openDecls }
  let cmdPos := pstate.pos
  let (cmd, next, messages) := Parser.parseCommand ictx pmctx pstate st.messages
  let st := { st with messages }
  let stop (st : Command.State) (v : Violation) : Result :=
    let message : Message :=
      { fileName := ictx.fileName, pos := ictx.fileMap.toPosition v.pos, severity := .error, data := v.text }
    { commandState := { st with messages := st.messages.add message }, rejected := some v.text }
  let mut st := st
  if ranges.any fun (s, e) => s < next.pos.byteIdx && cmdPos.byteIdx < e then
    if let some v := preCheck st.env scope ranges cmd then return stop st v
    let before : NameSet := (← st.env.getLocalConstantInfos).foldl (·.insert ·.name) {}
    st ← elabCommand ictx cmdPos cmd st
    if let some text ← postCheck before st.env then return stop st ⟨cmd.getPos?.getD cmdPos, text⟩
  else
    st ← elabCommand ictx cmdPos cmd st
  if Parser.isTerminalCommand cmd then return { commandState := st }
  run ictx ranges next st

end AIFunctionsConfine
