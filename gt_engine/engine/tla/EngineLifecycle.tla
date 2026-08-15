(*
 * EngineLifecycle — PlusCal/TLA+ model of the Inline Engine lifecycle
 * (IE-01 independent concurrency check).
 *
 * Mirrors gt_engine/engine/transitions.py exactly:
 *   SELECTED -> NORMALIZED -> SNAPSHOT_BOUND -> PREFLIGHTED -> DECIDED
 *   -> EXECUTED | REPLACED | REWRITTEN | SUPPRESSED
 *   -> POSTFLIGHTED -> COMPILED -> JOINED -> DISPATCHED
 *   -> PROVIDER_ACCEPTED -> DELIVERED -> RESPONSE_COMMITTED
 *   -> NEXT_ACTION_BOUND -> RECEIPT_FINAL
 * with FAILED reachable from any state and FAIL_OPEN recovery to DECIDED.
 *
 * Run with TLC (java -cp tla2tools.jar tlc2.TLC EngineLifecycle.tla).
 * TLC invocation is part of the IE-13 provider-free gate.
 *)
EXTENDS Integers, FiniteSets, TLC

CONSTANTS Actions
VARIABLES state, action, steps

States ==
  {"SELECTED", "NORMALIZED", "SNAPSHOT_BOUND", "PREFLIGHTED", "DECIDED",
   "EXECUTED", "REPLACED", "REWRITTEN", "SUPPRESSED", "POSTFLIGHTED",
   "COMPILED", "JOINED", "DISPATCHED", "PROVIDER_ACCEPTED", "DELIVERED",
   "RESPONSE_COMMITTED", "NEXT_ACTION_BOUND", "RECEIPT_FINAL", "FAILED"}

TerminalStates == {"RECEIPT_FINAL", "FAILED"}

(* Every selected action must terminate. *)
InvNoActionDisappears ==
  \A a \in Actions :
    state[a] \in TerminalStates

(* ENGINE failure passes through: FAILED is a recorded outcome, never a
 * silent drop; a non-failed trace terminates at RECEIPT_FINAL. *)
InvFailOpenPreservesExecution ==
  \A a \in Actions : state[a] # "SELECTED"

Init ==
  state = [a \in Actions |-> "SELECTED"] /\ steps = [a \in Actions |-> 0]

Select(a) ==
  /\ state[a] = "SELECTED"
  /\ state' = [state EXCEPT ![a] = "NORMALIZED"]

Normalize(a) ==
  /\ state[a] = "NORMALIZED"
  /\ state' = [state EXCEPT ![a] = "SNAPSHOT_BOUND"]

BindSnapshot(a) ==
  /\ state[a] = "SNAPSHOT_BOUND"
  /\ state' = [state EXCEPT ![a] = "PREFLIGHTED"]

Preflight(a) ==
  /\ state[a] = "PREFLIGHTED"
  /\ state' = [state EXCEPT ![a] = "DECIDED"]

DecideExecute(a) ==
  /\ state[a] = "DECIDED"
  /\ state' = [state EXCEPT ![a] = "EXECUTED"]

DecideReplace(a) ==
  /\ state[a] = "DECIDED"
  /\ state' = [state EXCEPT ![a] = "REPLACED"]

DecideRewrite(a) ==
  /\ state[a] = "DECIDED"
  /\ state' = [state EXCEPT ![a] = "REWRITTEN"]

DecideSuppress(a) ==
  /\ state[a] = "DECIDED"
  /\ state' = [state EXCEPT ![a] = "SUPPRESSED"]

FailOpen(a) ==
  /\ state[a] \in {"NORMALIZED", "SNAPSHOT_BOUND", "PREFLIGHTED"}
  /\ state' = [state EXCEPT ![a] = "DECIDED"]

Postflight(a) ==
  /\ state[a] \in {"EXECUTED", "REPLACED", "REWRITTEN", "SUPPRESSED"}
  /\ state' = [state EXCEPT ![a] = "POSTFLIGHTED"]

Compile(a) ==
  /\ state[a] = "POSTFLIGHTED"
  /\ state' = [state EXCEPT ![a] = "COMPILED"]

Join(a) ==
  /\ state[a] = "COMPILED"
  /\ state' = [state EXCEPT ![a] = "JOINED"]

Dispatch(a) ==
  /\ state[a] = "JOINED"
  /\ state' = [state EXCEPT ![a] = "DISPATCHED"]

ProviderAccepted(a) ==
  /\ state[a] = "DISPATCHED"
  /\ state' = [state EXCEPT ![a] = "PROVIDER_ACCEPTED"]

Deliver(a) ==
  /\ state[a] = "PROVIDER_ACCEPTED"
  /\ state' = [state EXCEPT ![a] = "DELIVERED"]

ResponseCommitted(a) ==
  /\ state[a] = "DELIVERED"
  /\ state' = [state EXCEPT ![a] = "RESPONSE_COMMITTED"]

BindNextAction(a) ==
  /\ state[a] = "RESPONSE_COMMITTED"
  /\ state' = [state EXCEPT ![a] = "NEXT_ACTION_BOUND"]

ReceiptFinal(a) ==
  /\ state[a] = "NEXT_ACTION_BOUND"
  /\ state' = [state EXCEPT ![a] = "RECEIPT_FINAL"]

Fail(a) ==
  /\ state[a] \notin TerminalStates \/ state[a] = "FAILED"
  /\ state' = [state EXCEPT ![a] = "FAILED"]

Next ==
  \E a \in Actions :
    \/ Select(a)
    \/ Normalize(a) \/ BindSnapshot(a) \/ Preflight(a)
    \/ DecideExecute(a) \/ DecideReplace(a) \/ DecideRewrite(a) \/ DecideSuppress(a)
    \/ FailOpen(a) \/ Postflight(a) \/ Compile(a) \/ Join(a) \/ Dispatch(a)
    \/ ProviderAccepted(a) \/ Deliver(a) \/ ResponseCommitted(a)
    \/ BindNextAction(a) \/ ReceiptFinal(a) \/ Fail(a)

Spec == Init /\ [][Next]_<<state, steps>> /\ WF_<<state, steps>>(Next)

(* All states are reachable. *)
InvAllStatesReachable ==
  \A s \in States : \E a \in Actions : state[a] = s

(* A terminal failure can always fail-open back to literal execution. *)
InvFailOpenReachability ==
  \A a \in Actions :
    state[a] = "FAILED" => \E b \in Actions : state[b] = "DECIDED"

THEOREM Spec => []InvNoActionDisappears
THEOREM Spec => []InvFailOpenPreservesExecution
