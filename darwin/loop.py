from collections import deque
from agents.sre_agent import resolve_incident, DEFAULT_SYSTEM_PROMPT
from agents.judge import score_resolution
from darwin.mutator import mutate_prompt
from darwin.storage import save_resolution, save_generation
from darwin.arize_client import create_experiment
from darwin.vijil_dome import guard_incident_input, guard_resolution_output
from darwin.vijil_genome import ensure_agent, record_mutation
from config import DARWIN_TRIGGER_THRESHOLD, DARWIN_MAX_GENERATIONS, DARWIN_WINDOW_SIZE
from observability import get_tracer, span_ok


class DarwinLoop:
    def __init__(self, on_event=None):
        self.current_prompt = DEFAULT_SYSTEM_PROMPT
        self.generation = 0
        self.window: deque = deque(maxlen=DARWIN_WINDOW_SIZE)
        self.all_results: list[dict] = []
        self.on_event = on_event or (lambda e: None)

    def _emit(self, event_type: str, data: dict) -> None:
        self.on_event({"type": event_type, **data})

    def _rolling_avg(self) -> float:
        if not self.window:
            return 1.0
        return sum(self.window) / len(self.window)

    def run(self, incidents: list[dict], register_vijil: bool = True) -> list[dict]:
        tracer = get_tracer()

        # Register Darwin SRE in Vijil console (once per run)
        if register_vijil:
            try:
                ensure_agent(self.current_prompt)
            except Exception:
                pass  # Non-blocking

        with tracer.start_as_current_span("darwin.run") as run_span:
            run_span.set_attribute("generation", self.generation)
            run_span.set_attribute("total_incidents", len(incidents))

            for incident in incidents:
                # Vijil Dome: input guardrail
                input_guard = guard_incident_input(incident)
                if not input_guard.allowed:
                    self._emit("incident_blocked", {
                        "incident_id": incident["id"],
                        "reason": "vijil_dome_input",
                        "triggered": input_guard.triggered,
                    })
                    continue

                self._emit("incident_start", {
                    "incident_id": incident["id"],
                    "title": incident["title"],
                    "is_edge_case": incident.get("is_edge_case", False),
                    "generation": self.generation,
                    "dome_input_flagged": input_guard.flagged,
                })

                resolution = resolve_incident(incident, self.current_prompt)

                # Vijil Dome: output guardrail
                output_guard = guard_resolution_output(resolution)
                if output_guard.flagged:
                    # Log but don't block — just tag it
                    self._emit("resolution_flagged", {
                        "incident_id": incident["id"],
                        "triggered": output_guard.triggered,
                    })

                scores = score_resolution(incident, resolution)

                self.window.append(scores["composite"])
                save_resolution(incident, resolution, scores, self.generation)

                result = {
                    "incident": incident,
                    "resolution": resolution,
                    "scores": scores,
                    "generation": self.generation,
                    "rolling_avg": self._rolling_avg(),
                }
                self.all_results.append(result)

                self._emit("incident_resolved", {
                    "incident_id": incident["id"],
                    "scores": scores,
                    "rolling_avg": self._rolling_avg(),
                    "generation": self.generation,
                })

                rolling = self._rolling_avg()
                if (
                    len(self.window) == DARWIN_WINDOW_SIZE
                    and rolling < DARWIN_TRIGGER_THRESHOLD
                    and self.generation < DARWIN_MAX_GENERATIONS
                ):
                    self._run_darwin(result)

            run_span.set_attribute("final_generation", self.generation)
            run_span.set_attribute("final_rolling_avg", self._rolling_avg())
            span_ok(run_span)

        return self.all_results

    def _run_darwin(self, triggering_result: dict) -> None:
        tracer = get_tracer()
        self.generation += 1
        score_before = self._rolling_avg()

        recent_failures = [
            r for r in self.all_results[-20:]
            if r["scores"]["composite"] < DARWIN_TRIGGER_THRESHOLD
        ]

        self._emit("darwin_start", {
            "generation": self.generation,
            "score_before": score_before,
            "num_failures": len(recent_failures),
        })

        with tracer.start_as_current_span("darwin.evolve") as span:
            span.set_attribute("generation", self.generation)
            span.set_attribute("score_before", score_before)
            span.set_attribute("num_failures", len(recent_failures))
            span.set_attribute("failure_categories", str([
                r["incident"].get("category") for r in recent_failures
            ]))

            new_prompt, diff = mutate_prompt(self.current_prompt, recent_failures)

            # Re-evaluate on failed incidents with the new prompt
            re_scores = []
            for item in recent_failures[:5]:
                r = resolve_incident(item["incident"], new_prompt)
                s = score_resolution(item["incident"], r)
                re_scores.append(s["composite"])

            score_after = sum(re_scores) / len(re_scores) if re_scores else score_before
            improved = score_after >= score_before

            if improved:
                self.current_prompt = new_prompt

            failure_patterns = list({
                i["incident"].get("category", "unknown") for i in recent_failures
            })
            failed_ids = [i["incident"]["id"] for i in recent_failures[:5]]

            save_generation(
                generation_id=self.generation,
                system_prompt=new_prompt,
                prompt_diff=diff,
                score_before=score_before,
                score_after=score_after,
                failed_incident_ids=failed_ids,
                failure_patterns=failure_patterns,
            )

            self.window.clear()

            span.set_attribute("score_after", score_after)
            span.set_attribute("improved", improved)
            span.set_attribute("prompt_diff_len", len(diff))
            span_ok(span)

        # Create Arize experiment for this generation so scores appear in the dashboard
        try:
            create_experiment(self.generation, self.all_results)
        except Exception:
            pass  # Non-blocking — Darwin continues even if Arize is unavailable

        # Record mutation in Vijil genome lineage
        try:
            record_mutation(self.generation, new_prompt, score_before, score_after)
        except Exception:
            pass  # Non-blocking

        self._emit("darwin_complete", {
            "generation": self.generation,
            "score_before": score_before,
            "score_after": score_after,
            "prompt_diff": diff,
            "prompt_improved": improved,
        })
