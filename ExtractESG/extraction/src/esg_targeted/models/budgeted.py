"""Shared report-budget adapter for inference outside the disclosure Agent.

The Agent owns accounting and protocol validation for generate_structured, so
that method deliberately passes through. Every other exposed inference entry
below consumes the same durable ledger, regardless of provider or role.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from esg_targeted.contracts import SemanticDecision
from esg_targeted.models.errors import ModelRunFailure
from esg_targeted.models.family_adjudication import FamilyAdjudicationDecision
from esg_targeted.models.token_usage import resolve_token_usage
from esg_targeted.models.token_admission import estimate_input_tokens, bind_runtime_admission
from esg_targeted.runtime_budget import ReportRuntimeBudget, task_input_fingerprint


def _json_value(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items() if not callable(item)}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value if not callable(item)]
    return value


class BudgetedModel:
    def __init__(self, model: Any, budget: ReportRuntimeBudget, *, role: str = "semantic_brain"):
        self._delegate, self._budget, self._role = model, budget, role

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def __getattribute__(self, name):
        if name in {"decide", "readjudicate_family", "resolve_conflicts", "generate_structured", "close"}:
            getattr(object.__getattribute__(self, "_delegate"), name)
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        if name in {"_delegate", "_budget", "_role"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._delegate, name, value)

    def close(self):
        return self._delegate.close()

    def generate_structured(self, *args, **kwargs):
        return self._delegate.generate_structured(*args, **kwargs)

    def decide(self, packet, **kwargs):
        return self._invoke("decide", packet, **kwargs)

    def readjudicate_family(self, packet, **kwargs):
        return self._invoke("readjudicate_family", packet, **kwargs)

    def resolve_conflicts(self, *args, **kwargs):
        return self._invoke("resolve_conflicts", *args, **kwargs)

    def _reservation_tokens(self, method, args, kwargs):
        output = int(getattr(self._delegate, "max_tokens", None) or getattr(self._delegate, "max_output_tokens", None) or 4096)
        context = _json_value({"args": args, "kwargs": kwargs})
        text = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
        if method == "decide" and args:
            packet = args[0]
            text = getattr(packet, "model_context", text)
            compiler = getattr(self._delegate, "template_compiler", None)
            if compiler:
                text += json.dumps(compiler.compile(packet), ensure_ascii=False, default=str)
                text += compiler.instructions(kwargs.get("feedback"))
            elif kwargs.get("feedback"):
                text += str(kwargs["feedback"])
            calculator = getattr(self._delegate, "_output_token_budget", None)
            if callable(calculator):
                output = int(calculator(packet, visual_used=bool(kwargs.get("use_visual") and getattr(packet, "page_image_paths", []))))
        elif method == "readjudicate_family" and args:
            output = min(output, max(int(getattr(self._delegate, "min_output_tokens", 2048)), len(getattr(args[0], "cases", [])) * 512))
        image_count = int(bool(method == "decide" and args and kwargs.get("use_visual")
                               and getattr(args[0], "page_image_paths", [])))
        # No bytes-as-tokens gate. The runtime refines this estimate using the
        # actual adapter prompt and image tensors before inference begins.
        admission = replace(estimate_input_tokens(text, image_count=image_count),
                            runtime_refinement=callable(getattr(self._delegate, "_prepare_runtime_inputs", None)))
        return admission, output, context

    def _invoke(self, method, *args, **kwargs):
        admission, output_budget, context = self._reservation_tokens(method, args, kwargs)
        scope = task_input_fingerprint(f"{self._role}:{method}", context)
        reservation = self._budget.begin_call(f"{self._role}:{method}", input_tokens=admission.tokens,
            max_output_tokens=output_budget, scope=scope, input_token_basis=admission.basis,
            input_admission=admission.to_dict())
        call_id = reservation["call_id"]
        try:
            with bind_runtime_admission(self._delegate, self._budget, call_id):
                result = getattr(self._delegate, method)(*args, **kwargs)
            usage = resolve_token_usage(result, input_reservation=admission.tokens,
                input_reservation_basis=admission.basis, output_reservation=output_budget)
            self._budget.finish_call(call_id, **usage.finish_kwargs(), outcome="returned")
            if getattr(result, "finish_reason", None) == "length":
                raise ModelRunFailure("Generation reached its output token limit; split the source task before retrying",
                    category="generation_incomplete", raw_output=getattr(result, "raw_output", ""),
                    telemetry={"prompt_tokens": usage.input_tokens, "generation_tokens": usage.output_tokens,
                        "input_token_basis": usage.input_token_basis, "output_token_basis": usage.output_token_basis,
                        "finish_reason": "length"})
            decision = getattr(result, "decision", None) or (result if isinstance(result, SemanticDecision) else None)
            if method == "decide":
                if not isinstance(decision, SemanticDecision):
                    raise ModelRunFailure("Model returned an invalid SemanticDecision protocol", category="protocol")
                if decision.uncertainty_code == "model_output_invalid":
                    self._budget.record_validation(call_id, outcome="error", failure_signature="protocol:model_output_invalid")
                else:
                    self._budget.record_validation(call_id, outcome="success")
            elif method == "readjudicate_family":
                if not isinstance(decision, FamilyAdjudicationDecision):
                    raise ModelRunFailure("Model returned an invalid family adjudication protocol", category="protocol")
                self._budget.record_validation(call_id, outcome="success")
            else:
                status = getattr(decision, "status", None) or (result.get("status") if isinstance(result, dict) else None)
                if status in {"failed", "error", "model_output_invalid"}:
                    self._budget.record_validation(call_id, outcome="error", failure_signature=f"protocol:{status}")
                elif not isinstance(decision, (SemanticDecision, FamilyAdjudicationDecision)) and status not in {"found", "partial", "not_found", "ambiguous", "completed", "decided", "uncertain"}:
                    raise ModelRunFailure("Model returned an invalid conflict resolution protocol", category="protocol")
                else:
                    self._budget.record_validation(call_id, outcome="success")
            return result
        except BaseException as exc:
            telemetry = getattr(exc, "telemetry", {})
            usage = resolve_token_usage(telemetry, input_reservation=admission.tokens,
                input_reservation_basis=admission.basis, output_reservation=output_budget,
                raw_output=getattr(exc, "raw_output", ""))
            category = getattr(exc, "category", "runtime")
            self._budget.finish_call(call_id, **usage.finish_kwargs(),
                outcome="error", failure_signature=f"{category}:{exc}")
            # If generation already returned, finish is idempotent and only
            # this protocol validation changes the failure circuit.
            self._budget.record_validation(call_id, outcome="error", failure_signature=f"{category}:{exc}")
            raise
