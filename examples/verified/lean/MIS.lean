/-! # Maximum independent set

An *independent set* of a graph is a set of vertices no two of which are joined
by an edge. The task: find the size of the largest one, and prove it is the
largest.

The solver tool is exact but capped: it refuses graphs above a size limit,
while the contract is about the *whole* graph. The way from one to the other is
to shrink the graph with reduction theorems, whose side conditions are checks
on a concrete graph that `decide` closes, and to consult the solver only on the
pieces it accepts. -/

namespace MIS

/-- A finite graph as a pair `(verts, edges)`: vertex labels and a list of
edges. Labels need not be contiguous, so a subgraph keeps its labels. -/
abbrev Graph := List Nat × List (Nat × Nat)

/-- `S` is an independent set of `G`: it lists each vertex at most once, only
vertices of `G`, and no edge of `G` has both ends in `S`. -/
def Independent (G : Graph) (S : List Nat) : Prop :=
  S.Nodup ∧ (∀ v ∈ S, v ∈ G.1) ∧ ∀ e ∈ G.2, ¬(e.1 ∈ S ∧ e.2 ∈ S)

/-- `k` is the size of a largest independent set of `G`: some independent set
has `k` vertices, and none has more. This speaks about every list of vertices,
so it cannot be checked by computation: prove it with the reduction theorems
below and the solver's guarantee. -/
def MaxIndependentSize (G : Graph) (k : Nat) : Prop :=
  (∃ S, Independent G S ∧ S.length = k) ∧ ∀ S, Independent G S → S.length ≤ k

/-- **The contract.** `size` is the size of a largest independent set of the
graph with vertices `verts` and edges `edges`. -/
def Contract (size : Nat) (verts : List Nat) (edges : List (Nat × Nat)) : Prop :=
  MaxIndependentSize (verts, edges) size

/-! ## Reduction theorems and their helpers -/

/-- The induced subgraph on the labels in `V`: keeps the vertices of `G` that
lie in `V` and the edges with both endpoints in `V`. -/
def induce (G : Graph) (V : List Nat) : Graph :=
  (G.1.filter (· ∈ V), G.2.filter fun e => e.1 ∈ V ∧ e.2 ∈ V)

/-- `G` minus vertices `u` and `v` and every incident edge. -/
def dropTwo (G : Graph) (u v : Nat) : Graph :=
  (G.1.filter fun w => w ≠ u ∧ w ≠ v,
   G.2.filter fun e => e.1 ≠ u ∧ e.1 ≠ v ∧ e.2 ≠ u ∧ e.2 ≠ v)

/-- Every list splits, in length, into the part satisfying `p` and the rest. -/
theorem length_filter_split (p : α → Bool) (l : List α) :
    l.length = (l.filter p).length + (l.filter fun a => !p a).length := by
  induction l with
  | nil => rfl
  | cons a t ih => cases h : p a <;> simp [h] <;> omega

/-- Nodup concatenation from disjointness. -/
theorem nodup_append_of {as bs : List α} (ha : as.Nodup) (hb : bs.Nodup)
    (hd : ∀ a ∈ as, a ∉ bs) : (as ++ bs).Nodup := by
  induction as with
  | nil => simpa
  | cons a t ih =>
    have h := List.pairwise_cons.mp ha
    refine List.pairwise_cons.mpr ⟨?_, ih h.2 fun x hx => hd x (List.mem_cons_of_mem _ hx)⟩
    intro b hb'
    rcases List.mem_append.mp hb' with hm | hm
    · exact h.1 b hm
    · exact fun he => hd a (List.mem_cons_self ..) (he ▸ hm)

/-- A nodup list over `{u, v}` that does not contain both has at most one
element. -/
theorem length_le_one_of_pair (l : List Nat) (u v : Nat) (hnd : l.Nodup)
    (hmem : ∀ x ∈ l, x = u ∨ x = v) (hnot : ¬(u ∈ l ∧ v ∈ l)) :
    l.length ≤ 1 := by
  match l with
  | [] => simp
  | [_] => simp
  | x :: y :: t =>
    exfalso
    have hxy : x ≠ y := (List.pairwise_cons.mp hnd).1 y (List.mem_cons_self ..)
    have hxm : x ∈ x :: y :: t := List.mem_cons_self ..
    have hym : y ∈ x :: y :: t := List.mem_cons.mpr (Or.inr (List.mem_cons_self ..))
    rcases hmem x hxm with hx | hx <;> rcases hmem y hym with hy | hy
    · exact hxy (hx.trans hy.symm)
    · exact hnot ⟨hx ▸ hxm, hy ▸ hym⟩
    · exact hnot ⟨hy ▸ hym, hx ▸ hxm⟩
    · exact hxy (hx.trans hy.symm)

/-! ### Base case: a single vertex -/

/-- A one-vertex, no-edge graph has maximum independent set size 1 — no solver
needed for a singleton component. -/
theorem max_singleton (v : Nat) : MaxIndependentSize ([v], []) 1 := by
  constructor
  · exact ⟨[v], ⟨by simp, by simp, by simp⟩, rfl⟩
  · intro S hS
    obtain ⟨hnd, hsub, -⟩ := hS
    match S, hnd with
    | [], _ => simp
    | [_], _ => simp
    | x :: y :: t, hnd =>
      have hx : x = v := by simpa using hsub x (by simp)
      have hy : y = v := by simpa using hsub y (by simp)
      have hxy : x ≠ y := (List.pairwise_cons.mp hnd).1 y (List.mem_cons_self ..)
      exact absurd (hx.trans hy.symm) hxy

/-! ### Split rule: no edge crosses `V₁`/`V₂` ⇒ optima add -/

/-- Decidable side condition for `max_of_split`: `V₁` and `V₂` are disjoint,
cover the vertices of `G`, and no edge of `G` crosses between them. On a
concrete graph, close it with `decide`. -/
def splitOK (G : Graph) (V₁ V₂ : List Nat) : Bool :=
  G.1.all (fun v => v ∈ V₁ ∨ v ∈ V₂) &&
  V₁.all (fun v => v ∉ V₂) &&
  G.2.all fun e => (e.1 ∈ V₁ ∧ e.2 ∈ V₁) ∨ (e.1 ∈ V₂ ∧ e.2 ∈ V₂)

/-- If `G` splits into two sides with no crossing edge, its optimum is the sum
of the optima of the two induced subgraphs. -/
theorem max_of_split (G : Graph) (V₁ V₂ : List Nat) (k₁ k₂ : Nat)
    (hok : splitOK G V₁ V₂ = true)
    (h₁ : MaxIndependentSize (induce G V₁) k₁)
    (h₂ : MaxIndependentSize (induce G V₂) k₂) :
    MaxIndependentSize G (k₁ + k₂) := by
  simp only [splitOK, Bool.and_eq_true, List.all_eq_true, decide_eq_true_eq] at hok
  obtain ⟨⟨hcover, hdisj⟩, hedge⟩ := hok
  have hverts₁ : ∀ v, v ∈ (induce G V₁).1 ↔ v ∈ G.1 ∧ v ∈ V₁ := by
    intro v; simp [induce, List.mem_filter]
  have hverts₂ : ∀ v, v ∈ (induce G V₂).1 ↔ v ∈ G.1 ∧ v ∈ V₂ := by
    intro v; simp [induce, List.mem_filter]
  have hedges₁ : ∀ e, e ∈ (induce G V₁).2 ↔ e ∈ G.2 ∧ e.1 ∈ V₁ ∧ e.2 ∈ V₁ := by
    intro e; simp [induce, List.mem_filter]
  have hedges₂ : ∀ e, e ∈ (induce G V₂).2 ↔ e ∈ G.2 ∧ e.1 ∈ V₂ ∧ e.2 ∈ V₂ := by
    intro e; simp [induce, List.mem_filter]
  constructor
  · -- existence: concatenate the two witnesses
    obtain ⟨S₁, ⟨hnd₁, hsub₁, hind₁⟩, hlen₁⟩ := h₁.1
    obtain ⟨S₂, ⟨hnd₂, hsub₂, hind₂⟩, hlen₂⟩ := h₂.1
    have hS₁V : ∀ v ∈ S₁, v ∈ V₁ := fun v hv => ((hverts₁ v).mp (hsub₁ v hv)).2
    have hS₂V : ∀ v ∈ S₂, v ∈ V₂ := fun v hv => ((hverts₂ v).mp (hsub₂ v hv)).2
    refine ⟨S₁ ++ S₂, ⟨?_, ?_, ?_⟩, by simp [hlen₁, hlen₂]⟩
    · exact nodup_append_of hnd₁ hnd₂ fun v hv₁ hv₂ => hdisj v (hS₁V v hv₁) (hS₂V v hv₂)
    · intro v hv
      rcases List.mem_append.mp hv with h | h
      · exact ((hverts₁ v).mp (hsub₁ v h)).1
      · exact ((hverts₂ v).mp (hsub₂ v h)).1
    · intro e he ⟨h1, h2⟩
      have side : ∀ w, w ∈ S₁ ++ S₂ → w ∈ V₁ → w ∈ S₁ := by
        intro w hw hwV
        rcases List.mem_append.mp hw with h | h
        · exact h
        · exact absurd (hS₂V w h) (hdisj w hwV)
      have side' : ∀ w, w ∈ S₁ ++ S₂ → w ∈ V₂ → w ∈ S₂ := by
        intro w hw hwV
        rcases List.mem_append.mp hw with h | h
        · exact absurd hwV (hdisj w (hS₁V w h))
        · exact h
      rcases hedge e he with ⟨hv1, hv2⟩ | ⟨hv1, hv2⟩
      · exact hind₁ e ((hedges₁ e).mpr ⟨he, hv1, hv2⟩) ⟨side _ h1 hv1, side _ h2 hv2⟩
      · exact hind₂ e ((hedges₂ e).mpr ⟨he, hv1, hv2⟩) ⟨side' _ h1 hv1, side' _ h2 hv2⟩
  · -- bound: any independent set splits along V₁
    intro S ⟨hnd, hsub, hind⟩
    have hb₁ : Independent (induce G V₁) (S.filter fun v => decide (v ∈ V₁)) := by
      refine ⟨hnd.filter _, ?_, ?_⟩
      · intro v hv
        obtain ⟨hvS, hvV⟩ := List.mem_filter.mp hv
        exact (hverts₁ v).mpr ⟨hsub v hvS, by simpa using hvV⟩
      · intro e he ⟨h1, h2⟩
        exact hind e ((hedges₁ e).mp he).1
          ⟨(List.mem_filter.mp h1).1, (List.mem_filter.mp h2).1⟩
    have hb₂ : Independent (induce G V₂) (S.filter fun v => !decide (v ∈ V₁)) := by
      refine ⟨hnd.filter _, ?_, ?_⟩
      · intro v hv
        obtain ⟨hvS, hvV⟩ := List.mem_filter.mp hv
        have hv₁ : v ∉ V₁ := by simpa using hvV
        have := hcover v (hsub v hvS)
        exact (hverts₂ v).mpr ⟨hsub v hvS, this.resolve_left hv₁⟩
      · intro e he ⟨h1, h2⟩
        exact hind e ((hedges₂ e).mp he).1
          ⟨(List.mem_filter.mp h1).1, (List.mem_filter.mp h2).1⟩
    have hlen := length_filter_split (fun v => decide (v ∈ V₁)) S
    have := h₁.2 _ hb₁
    have := h₂.2 _ hb₂
    omega

/-! ### Pendant rule: a degree-1 vertex is always picked, its neighbor never -/

/-- Decidable side condition for `max_of_pendant`: `v` is a vertex whose only
edges connect it to `u ≠ v`, and the `u`–`v` edge exists. On a concrete graph,
close it with `decide`. -/
def pendantOK (G : Graph) (u v : Nat) : Bool :=
  (u ∈ G.1) && (v ∈ G.1) && (u ≠ v) &&
  ((u, v) ∈ G.2 ∨ (v, u) ∈ G.2) &&
  G.2.all fun e => (e.1 = v → e.2 = u) ∧ (e.2 = v → e.1 = u)

/-- Removing a pendant vertex `v` and its sole neighbor `u` costs exactly one:
some optimum always contains `v` and never `u`. -/
theorem max_of_pendant (G : Graph) (u v k : Nat)
    (hok : pendantOK G u v = true)
    (h : MaxIndependentSize (dropTwo G u v) k) :
    MaxIndependentSize G (k + 1) := by
  simp only [pendantOK, Bool.and_eq_true, List.all_eq_true, decide_eq_true_eq] at hok
  obtain ⟨⟨⟨⟨-, hvG⟩, hne⟩, huv⟩, hdeg⟩ := hok
  have hverts : ∀ w, w ∈ (dropTwo G u v).1 ↔ w ∈ G.1 ∧ w ≠ u ∧ w ≠ v := by
    intro w; simp [dropTwo, List.mem_filter]
  have hedges : ∀ e, e ∈ (dropTwo G u v).2 ↔
      e ∈ G.2 ∧ e.1 ≠ u ∧ e.1 ≠ v ∧ e.2 ≠ u ∧ e.2 ≠ v := by
    intro e; simp [dropTwo, List.mem_filter]
  constructor
  · -- existence: v joins any optimum of the reduced graph
    obtain ⟨S, ⟨hnd, hsub, hind⟩, hlen⟩ := h.1
    have hvS : v ∉ S := fun hv => ((hverts v).mp (hsub v hv)).2.2 rfl
    have huS : u ∉ S := fun hu => ((hverts u).mp (hsub u hu)).2.1 rfl
    refine ⟨v :: S, ⟨by simp [List.nodup_cons, hvS, hnd], ?_, ?_⟩, by simp [hlen]⟩
    · intro w hw
      rcases List.mem_cons.mp hw with h | h
      · exact h ▸ hvG
      · exact ((hverts w).mp (hsub w h)).1
    · intro e he ⟨h1, h2⟩
      by_cases he1v : e.1 = v
      · have h2u : e.2 = u := (hdeg e he).1 he1v
        rcases List.mem_cons.mp h2 with h | h
        · exact hne (h2u.symm.trans h)
        · exact huS (h2u ▸ h)
      by_cases he2v : e.2 = v
      · have h1u : e.1 = u := (hdeg e he).2 he2v
        rcases List.mem_cons.mp h1 with h | h
        · exact hne (h1u.symm.trans h)
        · exact huS (h1u ▸ h)
      -- e touches neither v; if it touched u the pick set can't contain u
      by_cases he1u : e.1 = u
      · rcases List.mem_cons.mp h1 with h | h
        · exact he1v h
        · exact huS (he1u ▸ h)
      by_cases he2u : e.2 = u
      · rcases List.mem_cons.mp h2 with h | h
        · exact he2v h
        · exact huS (he2u ▸ h)
      -- e survives into the reduced graph
      have heD : e ∈ (dropTwo G u v).2 :=
        (hedges e).mpr ⟨he, he1u, he1v, he2u, he2v⟩
      have h1' : e.1 ∈ S := (List.mem_cons.mp h1).resolve_left he1v
      have h2' : e.2 ∈ S := (List.mem_cons.mp h2).resolve_left he2v
      exact hind e heD ⟨h1', h2'⟩
  · -- bound: strip u and v from any independent set; at most one was picked
    intro S ⟨hnd, hsub, hind⟩
    have hnotboth : ¬(u ∈ S ∧ v ∈ S) := by
      rcases huv with h | h
      · exact fun ⟨hu, hv⟩ => hind (u, v) h ⟨hu, hv⟩
      · exact fun ⟨hu, hv⟩ => hind (v, u) h ⟨hv, hu⟩
    have hb : Independent (dropTwo G u v) (S.filter fun w => decide (w ≠ u ∧ w ≠ v)) := by
      refine ⟨hnd.filter _, ?_, ?_⟩
      · intro w hw
        obtain ⟨hwS, hwok⟩ := List.mem_filter.mp hw
        have : w ≠ u ∧ w ≠ v := by simpa using hwok
        exact (hverts w).mpr ⟨hsub w hwS, this⟩
      · intro e he ⟨h1, h2⟩
        exact hind e ((hedges e).mp he).1
          ⟨(List.mem_filter.mp h1).1, (List.mem_filter.mp h2).1⟩
    have hlen := length_filter_split (fun w => decide (w ≠ u ∧ w ≠ v)) S
    have hdrop : (S.filter fun w => !decide (w ≠ u ∧ w ≠ v)).length ≤ 1 := by
      apply length_le_one_of_pair _ u v (hnd.filter _)
      · intro x hx
        have hxk := (List.mem_filter.mp hx).2
        by_cases hxu : x = u
        · exact Or.inl hxu
        by_cases hxv : x = v
        · exact Or.inr hxv
        · exfalso; simp [hxu, hxv] at hxk
      · intro ⟨hu, hv⟩
        exact hnotboth ⟨(List.mem_filter.mp hu).1, (List.mem_filter.mp hv).1⟩
    have := h.2 _ hb
    omega

/-! ## The solver, a tool -/

/-- The size of a largest independent set of a graph, as the exact solver tool
computes it. Each recorded call adds the value and the tool's guarantee
`MaxIndependentSize g (misSolver g)` for that one graph `g`; the tool refuses
graphs above its size cap, and a refused call records nothing. -/
opaque misSolver : Graph → Nat

end MIS
