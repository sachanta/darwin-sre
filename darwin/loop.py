from collections import deque
from agents.sre_agent import resolve_incident, DEFAULT_SYSTEM_PROMPT
from agents.judge import score_resolution
from darwin.mutator import generate_skill, generate_kb_article
from darwin.skills import retrieve_skills, save_skill, increment_skill_use, retire_stale_skills
from darwin.storage import (
    save_resolution, save_generation,
    save_alert, update_alert_status, save_kb_article,
)
from darwin.arize_client import create_experiment
from darwin.vijil_dome import guard_incident_input, guard_resolution_output
from darwin.vijil_genome import ensure_agent, record_mutation
from config import DARWIN_TRIGGER_THRESHOLD, DARWIN_MAX_GENERATIONS, DARWIN_WINDOW_SIZE
from observability import get_tracer, span_ok


class DarwinLoop:
    def __init__(self, on_event=None, run_id: str | None = None):
        self.current_prompt = DEFAULT_SYSTEM_PROMPT
        self.generation = 0
        self.window: deque = deque(maxlen=DARWIN_WINDOW_SIZE)
        self.all_results: list[dict] = []
        self.on_event = on_event or (lambda e: None)
        self.run_id = run_id

    def _emit(self, event_type: str, data: dict) -> None:
        self.on_event({"type": event_type, **data})

    def _rolling_avg(self) -> float:
        if not self.window:
            return 1.0
        return sum(self.window) / len(self.window)

    def run(self, incidents: list[dict], register_vijil: bool = True) -> list[dict]:
        tracer = get_tracer()

        if register_vijil:
            try:
                ensure_agent(self.current_prompt)
            except Exception:
                pass

        with tracer.start_as_current_span("darwin.run") as run_span:
            run_span.set_attribute("generation", self.generation)
            run_span.set_attribute("total_incidents", len(incidents))
            if self.run_id:
                run_span.set_attribute("run_id", self.run_id)

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

                # Retrieve context: skills + KB articles
                try:
                    from darwin.retrieval import retrieve_kbs
                    kb_articles = retrieve_kbs(incident)
                except Exception:
                    kb_articles = []

                active_skills = retrieve_skills(incident)
                for skill in active_skills:
                    increment_skill_use(skill["id"])

                self._emit("incident_start", {
                    "incident_id": incident["id"],
                    "title": incident["title"],
                    "is_edge_case": incident.get("is_edge_case", False),
                    "edge_case_family": incident.get("edge_case_family"),
                    "generation": self.generation,
                    "dome_input_flagged": input_guard.flagged,
                    "num_skills": len(active_skills),
                    "num_kb": len(kb_articles),
                })

                resolution = resolve_incident(
                    incident,
                    system_prompt=self.current_prompt,
                    skills=active_skills,
                    kb_articles=kb_articles,
                )

                # Vijil Dome: output guardrail
                output_guard = guard_resolution_output(resolution)
                if output_guard.flagged:
                    self._emit("resolution_flagged", {
                        "incident_id": incident["id"],
                        "triggered": output_guard.triggered,
                    })

                scores = score_resolution(incident, resolution)

                self.window.append(scores["composite"])
                save_resolution(
                    incident, resolution, scores, self.generation,
                    retrieved_kb_ids=[a["id"] for a in kb_articles],
                    run_id=self.run_id,
                )

                result = {
                    "incident": incident,
                    "resolution": resolution,
                    "scores": scores,
                    "generation": self.generation,
                    "rolling_avg": self._rolling_avg(),
                    "skills_used": [s["id"] for s in active_skills],
                    "kb_used": [a["id"] for a in kb_articles],
                }
                self.all_results.append(result)

                self._emit("incident_resolved", {
                    "incident_id": incident["id"],
                    "scores": scores,
                    "rolling_avg": self._rolling_avg(),
                    "generation": self.generation,
                    "skills_used": result["skills_used"],
                })

                rolling = self._rolling_avg()
                if (
                    len(self.window) == DARWIN_WINDOW_SIZE
                    and rolling < DARWIN_TRIGGER_THRESHOLD
                    and self.generation < DARWIN_MAX_GENERATIONS
                ):
                    # Raise alert before handing off to Darwin
                    failing_ids = [
                        r["incident"]["id"] for r in self.all_results[-DARWIN_WINDOW_SIZE:]
                    ]
                    alert_id = save_alert(
                        rolling_avg=rolling,
                        window_scores=list(self.window),
                        failing_incident_ids=failing_ids,
                        generation=self.generation,
                        run_id=self.run_id,
                    )
                    self._emit("alert_raised", {
                        "alert_id": alert_id,
                        "rolling_avg": rolling,
                        "window_scores": list(self.window),
                        "failing_incident_ids": failing_ids,
                    })
                    self._run_darwin(result, alert_id=alert_id)

            run_span.set_attribute("final_generation", self.generation)
            run_span.set_attribute("final_rolling_avg", self._rolling_avg())
            span_ok(run_span)

        return self.all_results

    def _run_darwin(self, triggering_result: dict, alert_id: str | None = None) -> None:
        tracer = get_tracer()
        self.generation += 1
        score_before = self._rolling_avg()

        recent_failures = [
            r for r in self.all_results[-20:]
            if r["scores"]["composite"] < DARWIN_TRIGGER_THRESHOLD
        ]
        failure_families = list({
            r["incident"].get("edge_case_family", "unknown") for r in recent_failures
        })

        self._emit("darwin_start", {
            "generation": self.generation,
            "score_before": score_before,
            "num_failures": len(recent_failures),
            "failure_families": failure_families,
            "alert_id": alert_id,
        })

        if alert_id:
            try:
                update_alert_status(alert_id, "improving")
            except Exception:
                pass

        with tracer.start_as_current_span("darwin.evolve") as span:
            span.set_attribute("generation", self.generation)
            span.set_attribute("score_before", score_before)
            span.set_attribute("num_failures", len(recent_failures))
            span.set_attribute("failure_families", str(failure_families))

            # 1. Generate a new Skill
            new_skill, skill_description = generate_skill(recent_failures, self.generation)
            save_skill(new_skill)

            span.set_attribute("new_skill_id", new_skill["id"])
            span.set_attribute("new_skill_name", new_skill["name"])
            span.set_attribute("new_skill_tags", str(new_skill["tags"]))

            # 2. Write + index a new KB article so replay retrieval surfaces it
            new_kb_article_id = None
            try:
                kb_article = generate_kb_article(recent_failures, self.generation, new_skill["name"])
                from darwin.retrieval import index_kb_article
                kb_article = index_kb_article(kb_article)   # embeds + upserts → adds embedding field
                save_kb_article(kb_article)
                new_kb_article_id = kb_article["id"]
                span.set_attribute("new_kb_article_id", new_kb_article_id)
                self._emit("kb_article_written", {
                    "generation": self.generation,
                    "kb_article_id": new_kb_article_id,
                    "title": kb_article.get("title", ""),
                })
            except Exception as exc:
                span.set_attribute("kb_write_error", str(exc))

            # 3. Replay: re-run failed incidents — retrieval now surfaces the new KB
            re_scores = []
            for item in recent_failures[:5]:
                try:
                    from darwin.retrieval import retrieve_kbs
                    kb = retrieve_kbs(item["incident"])
                except Exception:
                    kb = []
                replay_skills = retrieve_skills(item["incident"]) + [new_skill]
                r = resolve_incident(item["incident"], self.current_prompt,
                                     skills=replay_skills, kb_articles=kb)
                s = score_resolution(item["incident"], r)
                re_scores.append(s["composite"])

            score_after = sum(re_scores) / len(re_scores) if re_scores else score_before
            improved = score_after >= score_before

            failed_ids = [i["incident"]["id"] for i in recent_failures[:5]]
            save_generation(
                generation_id=self.generation,
                system_prompt=self.current_prompt,
                prompt_diff=f"[skill written] {skill_description}",
                score_before=score_before,
                score_after=score_after,
                failed_incident_ids=failed_ids,
                failure_patterns=failure_families,
                new_kb_article_id=new_kb_article_id,
                run_id=self.run_id,
            )

            retired = retire_stale_skills(self.generation)
            self.window.clear()

            span.set_attribute("score_after", score_after)
            span.set_attribute("improved", improved)
            span.set_attribute("skills_retired", str(retired))
            span_ok(span)

        # Resolve alert if replay improved the score
        if alert_id:
            try:
                if improved:
                    update_alert_status(alert_id, "resolved")
                    self._emit("alert_resolved", {
                        "alert_id": alert_id,
                        "generation": self.generation,
                        "score_before": score_before,
                        "score_after": score_after,
                    })
            except Exception:
                pass

        # Non-blocking: Arize experiment + Vijil genome snapshot
        try:
            create_experiment(self.generation, self.all_results)
        except Exception:
            pass
        try:
            record_mutation(self.generation, self.current_prompt, score_before, score_after)
        except Exception:
            pass

        self._emit("darwin_complete", {
            "generation": self.generation,
            "score_before": score_before,
            "score_after": score_after,
            "improved": improved,
            "new_skill": {
                "id": new_skill["id"],
                "name": new_skill["name"],
                "guidance": new_skill["guidance"],
                "tags": new_skill["tags"],
            },
            "new_kb_article_id": new_kb_article_id,
            "skills_retired": retired,
            "prompt_changed": False,
        })
