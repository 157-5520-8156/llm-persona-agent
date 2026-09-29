#!/usr/bin/env python3
"""Offline life-materials author request and actor-scoped archive compiler."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

def main(argv=None):
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument, digest
    from companion_daemon.world_v2.prehistory_authoring import (
        PrehistoryAuthoringBrief, PrehistorySemanticReview, bind_semantic_review,
        package_reviewed, semantic_review_request, validate_draft,
    )
    from companion_daemon.world_v2.prehistory_life_materials import (
        LifeMaterialsDraft, author_request, export_actor_archive, ingestion_request,
    )
    from companion_daemon.world_v2.prehistory_life_materials_runner import (
        _captured_completion, _private_json, _response_content,
        provider_usage_audit, run_faithful_ingestion, run_story_linking,
    )
    from companion_daemon.world_v2.prehistory_story_links import (
        PrehistoryStoryLinkArtifact, carry_forward_approved_story_links,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materials")
    parser.add_argument("--actor-ref")
    parser.add_argument("--archive-id")
    parser.add_argument("--output", required=True)
    parser.add_argument("--as-of")
    parser.add_argument("--brief", help="PrehistoryAuthoringBrief JSON")
    parser.add_argument("--author-request", action="store_true")
    parser.add_argument("--narrative", help="Optional novel/story text supplied as data")
    parser.add_argument("--ingest-narrative", action="store_true",
                        help="Call a model in faithful extraction mode; this is separate from creative authoring")
    parser.add_argument("--ingest-request", action="store_true",
                        help="Write the faithful extraction packet for inspection without making a model call")
    parser.add_argument("--run-dir", help="Private capture directory for faithful ingestion (must be dedicated to this run)")
    parser.add_argument("--model", default="deepseek-flash", help="DeepSeek model for author and optional separate review call")
    parser.add_argument("--reviewer-model", help="Optional distinct model identity for the separate semantic review call")
    parser.add_argument("--semantic-review", action="store_true",
                        help="Make a separate model review call and bind its actual response; does not retain/import")
    parser.add_argument("--revision-materials", help="Prior LifeMaterialsDraft for reviewer-guided correction")
    parser.add_argument("--revision-archive", help="Exact prior unreviewed PrehistoryArchiveDocument")
    parser.add_argument("--revision-review", help="Bound rejected independent review for that exact archive")
    parser.add_argument("--revision-story-links", help="Prior link artifact with structured independent per-edge verdicts")
    args = parser.parse_args(argv)
    output = Path(args.output)
    revision_paths = (args.revision_materials, args.revision_archive, args.revision_review, args.revision_story_links)
    if any(revision_paths) and (not args.ingest_narrative or not all(revision_paths)):
        parser.error("review-guided revision requires --ingest-narrative and all four --revision-* inputs")
    if args.ingest_request:
        if not args.brief or not args.narrative or args.materials or args.actor_ref or args.archive_id or args.as_of or args.author_request or args.ingest_narrative:
            parser.error("--ingest-request requires --brief and --narrative, and cannot be combined with other modes")
        if output.exists():
            parser.error("output already exists; refusing to overwrite a private source packet")
        brief = PrehistoryAuthoringBrief.model_validate_json(Path(args.brief).read_text(encoding="utf-8"))
        packet = ingestion_request(brief, Path(args.narrative).read_text(encoding="utf-8"))
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
        os.chmod(output, 0o600)
        print(json.dumps({"written": str(output.resolve()), "mode": packet["mode"],
                          "source_sha256": packet["source"]["sha256"],
                          "source_bytes": packet["source"]["byte_length"],
                          "segments": len(packet["source"]["segments"]),
                          "packet_bytes": len(json.dumps(packet, ensure_ascii=False).encode("utf-8")),
                          "provider_calls": 0}, ensure_ascii=False))
        return
    if args.ingest_narrative:
        if (not args.brief or not args.narrative or not args.run_dir or not args.actor_ref or not args.archive_id
                or args.materials or args.as_of or args.author_request or args.ingest_request):
            parser.error("--ingest-narrative requires --brief, --narrative, --run-dir, --actor-ref and --archive-id")
        existing_manifest = Path(args.run_dir) / "manifest.json"
        can_resume = existing_manifest.exists() and json.loads(existing_manifest.read_text(encoding="utf-8")).get("status") in {"started", "repair_pending", "provider_or_runner_failed", "completed"}
        if output.exists() and not can_resume:
            parser.error("output already exists; refusing to spend model calls for an overwrite")
        brief = PrehistoryAuthoringBrief.model_validate_json(Path(args.brief).read_text(encoding="utf-8"))
        narrative_text = Path(args.narrative).read_text(encoding="utf-8")
        previous_materials = previous_archive = revision_review = previous_story_links = None
        if args.revision_materials:
            from companion_daemon.world_v2.prehistory_authoring import PrehistoryCreationReview

            previous_materials = LifeMaterialsDraft.model_validate_json(
                Path(args.revision_materials).read_text(encoding="utf-8"),
            )
            previous_archive = PrehistoryArchiveDocument.model_validate_json(
                Path(args.revision_archive).read_text(encoding="utf-8"),
            )
            revision_review = PrehistoryCreationReview.model_validate_json(
                Path(args.revision_review).read_text(encoding="utf-8"),
            )
            previous_story_links = PrehistoryStoryLinkArtifact.model_validate_json(
                Path(args.revision_story_links).read_text(encoding="utf-8"),
            )
        # Isolated prehistory work uses the dedicated debug balance. Never print
        # the resolved credential or include it in captured request metadata.
        from dotenv import dotenv_values
        local_env = dotenv_values(ROOT / ".env")
        api_key = (os.environ.get("DEEPSEEK_DEBUG_API_KEY") or local_env.get("DEEPSEEK_DEBUG_API_KEY") or "").strip()
        if not api_key:
            parser.error("faithful ingestion requires DEEPSEEK_DEBUG_API_KEY (environment or project .env)")
        base_url = os.environ.get("DEEPSEEK_BASE_URL") or local_env.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        materials, document, manifest = asyncio.run(run_faithful_ingestion(
            brief=brief, narrative_text=narrative_text, actor_ref=args.actor_ref, archive_id=args.archive_id,
            run_dir=Path(args.run_dir), api_key=api_key, base_url=base_url, model_name=args.model,
            previous_materials=previous_materials, previous_archive=previous_archive,
            revision_review=revision_review,
        ))
        story_link_artifact_path = Path(args.run_dir) / "story-linking" / "story-links.json"
        try:
            materials, story_link_manifest = asyncio.run(run_story_linking(
                materials=materials, actor_ref=args.actor_ref, narrative_text=narrative_text,
                run_dir=Path(args.run_dir),
                api_key=api_key, base_url=base_url, model_name=args.model,
                reviewed_link_findings=(
                    tuple(
                        f"rejected story relation {item.left_record_id} -> {item.right_record_id} "
                        f"({item.relation_kind}): {item.rationale}"
                        for item in revision_review.story_links if item.verdict == "reject"
                    ) + revision_review.cross_record_findings
                    if revision_review is not None else ()
                ),
            ))
        except Exception as link_error:
            if previous_story_links is None or previous_materials is None or previous_archive is None or revision_review is None:
                raise
            previous_source_materials = previous_materials.model_copy(update={
                "recollections": tuple(
                    record.model_copy(update={"related_record_refs": ()})
                    for record in previous_materials.recollections
                ),
            })
            current_archive = export_actor_archive(materials, args.actor_ref, args.archive_id)
            materials, carry_artifact, story_link_manifest = carry_forward_approved_story_links(
                previous_source_materials=previous_source_materials,
                previous_linked_materials=previous_materials,
                previous_archive=previous_archive,
                previous_artifact=previous_story_links,
                previous_review=revision_review,
                current_materials=materials,
                current_archive=current_archive,
                actor_ref=args.actor_ref,
                narrative_text=narrative_text,
            )
            carry_dir = Path(args.run_dir) / "story-link-carry-forward"
            carry_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(carry_dir, 0o700)
            failure_data = {
                "type": type(link_error).__name__,
                "message": str(link_error).replace(api_key, "[redacted]")[:1000],
            }
            failed_model_manifest_path = Path(args.run_dir) / "story-linking" / "manifest.json"
            if failed_model_manifest_path.exists():
                failed_model_manifest = json.loads(failed_model_manifest_path.read_text(encoding="utf-8"))
                failure_data["status"] = failed_model_manifest.get("status")
                failure_data["billing_state"] = failed_model_manifest.get("billing_state")
                failure_data["usage_audit"] = failed_model_manifest.get(
                    "attempt_usage_audit", failed_model_manifest.get("usage_audit"),
                )
            story_link_manifest["failed_model_generation"] = failure_data
            _private_json(carry_dir / "carry-forward-request.json", story_link_manifest["request"])
            _private_json(carry_dir / "story-links.json", json.loads(carry_artifact.model_dump_json()))
            _private_json(carry_dir / "manifest.json", story_link_manifest)
            story_link_artifact_path = carry_dir / "story-links.json"
        linked_materials_path = Path(args.run_dir) / "linked-materials.json"
        if linked_materials_path.exists():
            existing_linked = LifeMaterialsDraft.model_validate_json(linked_materials_path.read_text(encoding="utf-8"))
            if digest(existing_linked) != digest(materials):
                parser.error("existing linked materials differ from the captured story-link result")
        else:
            _private_json(linked_materials_path, json.loads(materials.model_dump_json()))
        document = export_actor_archive(materials, args.actor_ref, args.archive_id)
        link_summary = {
            "status": story_link_manifest.get("status"),
            "origin": story_link_manifest.get("origin"),
            "links": story_link_manifest.get("links"),
            "dropped_duplicate_relations": story_link_manifest.get("dropped_duplicate_relations"),
            "carried_relation_indexes": story_link_manifest.get("carried_relation_indexes"),
            "failed_model_generation": story_link_manifest.get("failed_model_generation"),
            "source_materials_hash": story_link_manifest.get("source_materials_hash"),
            "linked_materials_hash": story_link_manifest.get("linked_materials_hash"),
            "usage_audit": story_link_manifest.get("usage_audit"),
        }
        run_manifest_path = Path(args.run_dir) / "manifest.json"
        if run_manifest_path.exists():
            run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
            run_manifest["story_linking"] = link_summary
            with run_manifest_path.open("w", encoding="utf-8") as stream:
                json.dump(run_manifest, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
            os.chmod(run_manifest_path, 0o600)
        if not args.brief:
            parser.error("faithful ingestion requires a bound brief")
        validate_draft(brief.model_copy(update={"draft_source_ref": document.source_artifact_ref}), document)
        if output.exists():
            existing = PrehistoryArchiveDocument.model_validate_json(output.read_text(encoding="utf-8"))
            if digest(existing) != digest(document):
                parser.error("existing output differs from the captured ingestion; refusing overwrite")
        else:
            with output.open("x", encoding="utf-8") as stream:
                stream.write(document.model_dump_json(indent=2) + "\n")
            os.chmod(output, 0o600)
        result = {"written": str(output.resolve()), "mode": manifest.get("mode", "faithful_source_extraction"),
                  "archive_id": document.archive_id, "actor_ref": document.actor_ref,
                  "records": len(document.records), "materials_sha256": digest(materials),
                  "archive_sha256": digest(document), "run_dir": str(Path(args.run_dir).resolve()),
                  "author_model": args.model, "author_usage": manifest.get("usage_audit", []),
                  "story_links": {key: link_summary.get(key) for key in (
                      "status", "origin", "links", "carried_relation_indexes",
                      "failed_model_generation", "usage_audit",
                  )},
                  "review": "not_requested"}
        if args.semantic_review:
            # Source in the creation brief is the manuscript; the export is
            # bound to normalized material bytes. Review gets a distinct brief
            # whose source binding matches the immutable document under review.
            review_brief = brief.model_copy(update={"draft_source_ref": document.source_artifact_ref})
            review_dir = Path(args.run_dir) / "semantic-review"
            review_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            story_links = None
            if story_link_artifact_path.exists():
                story_link_capture = json.loads(story_link_artifact_path.read_text(encoding="utf-8"))
                story_links = PrehistoryStoryLinkArtifact.model_validate_json(
                    json.dumps(story_link_capture, ensure_ascii=False),
                )
            request = semantic_review_request(
                review_brief, document, story_links=story_links,
                narrative_text=narrative_text, source_materials=materials,
            )
            review_request_path = review_dir / "submitted-request.json"
            if not review_request_path.exists():
                _private_json(review_request_path, request)
            elif json.loads(review_request_path.read_text(encoding="utf-8")) != request:
                parser.error("existing review capture is bound to different content")
            review_messages = [
                {"role": "system", "content": request["instruction"] + "\n只审核给定内容，不要补写事实。"},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ]
            review_messages_path = review_dir / "submitted-messages.json"
            if not review_messages_path.exists():
                _private_json(review_messages_path, review_messages)
            elif json.loads(review_messages_path.read_text(encoding="utf-8")) != review_messages:
                parser.error("existing review messages differ from the bound request")
            captured_usage = []
            def observe_review(usage):
                captured_usage.append({
                    "provider": getattr(usage, "provider", "deepseek"),
                    "model": getattr(usage, "model", args.reviewer_model or args.model),
                    "purpose": getattr(usage, "purpose", ""),
                    "status": getattr(usage, "status", "unknown"),
                    "latency_ms": getattr(usage, "latency_ms", None),
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(usage, "completion_tokens", 0),
                    "reasoning_tokens": getattr(usage, "reasoning_tokens", 0),
                    "cache_hit_tokens": getattr(usage, "cache_hit_tokens", 0),
                    "cache_miss_tokens": getattr(usage, "cache_miss_tokens", 0),
                    "billing_state": getattr(usage, "billing_state", "unknown"),
                })
            async def call_review():
                return await _captured_completion(
                    api_key=api_key, base_url=base_url, model_name=args.reviewer_model or args.model,
                    messages=review_messages, attempt_dir=review_dir / "attempt-01",
                    usage_observer=observe_review,
                )
            review_result_path = review_dir / "review-result.json"
            if review_result_path.exists():
                saved_review = json.loads(review_result_path.read_text(encoding="utf-8"))
                raw_review = saved_review["response_content"]
                review_usage = saved_review.get("provider_usage", saved_review.get("usage", {}))
            else:
                review_attempt_dir = review_dir / "attempt-01"
                wire_response_path = review_attempt_dir / "provider-response.json"
                wire_request_path = review_attempt_dir / "provider-request.json"
                if wire_response_path.exists():
                    raw_review, review_usage = _response_content(json.loads(wire_response_path.read_text(encoding="utf-8")))
                elif wire_request_path.exists():
                    parser.error("review request was emitted without a captured response; billing is unknown and retry is disabled")
                else:
                    try:
                        raw_review, review_usage = asyncio.run(call_review())
                    except Exception as exc:
                        failure_path = review_dir / "failure.json"
                        if not failure_path.exists():
                            _private_json(failure_path, {
                                "type": type(exc).__name__,
                                "message": str(exc).replace(api_key, "[redacted]")[:2000],
                                "billing": "unknown_if_request_was_emitted",
                                "usage_audit": provider_usage_audit(
                                    args.reviewer_model or args.model, {},
                                    captured_usage[-1] if captured_usage else {},
                                ),
                            })
                        raise
                audit = provider_usage_audit(args.reviewer_model or args.model, review_usage,
                                             captured_usage[-1] if captured_usage else None)
                saved_review = {
                    "status": "response_captured", "response_content": raw_review,
                    **audit, "reviewer_model": args.reviewer_model or args.model,
                    "same_model_as_author": (args.reviewer_model or args.model) == args.model,
                }
                _private_json(review_result_path, saved_review)
            semantic = PrehistorySemanticReview.model_validate_json(raw_review)
            bound_review_path = review_dir / "bound-review.json"
            if bound_review_path.exists():
                from companion_daemon.world_v2.prehistory_authoring import PrehistoryCreationReview
                review = PrehistoryCreationReview.model_validate_json(bound_review_path.read_text(encoding="utf-8"))
            else:
                review = bind_semantic_review(request, semantic,
                    reviewer_ref=f"model:{args.reviewer_model or args.model}:prehistory-semantic-review.1",
                    reviewed_at=datetime.now().astimezone())
                _private_json(bound_review_path, json.loads(review.model_dump_json()))
            independent_model_id = (args.reviewer_model or args.model) != args.model
            if review.decision == "approved" and independent_model_id:
                package = package_reviewed(brief.model_copy(update={"draft_source_ref": document.source_artifact_ref}),
                                           document, review,
                                           review_artifact_ref=f"prehistory-semantic-review:{digest(request)}")
                package_path = review_dir / "reviewed-package.json"
                if package_path.exists():
                    from companion_daemon.world_v2.character_prehistory import ReviewedPrehistoryArchive
                    old_package = ReviewedPrehistoryArchive.model_validate_json(package_path.read_text(encoding="utf-8"))
                    if digest(old_package) != digest(package):
                        parser.error("existing reviewed package differs from the bound review")
                else:
                    _private_json(package_path, json.loads(package.model_dump_json()))
            result["review"] = {"decision": review.decision, "reviewer_ref": review.reviewer_ref,
                                "same_model_as_author": not independent_model_id,
                                "usage": {"provider_usage": review_usage,
                                          "usage_observer": saved_review.get("usage_observer"),
                                          "cost": saved_review.get("cost")},
                                "package_written": review.decision == "approved" and independent_model_id,
                                "package_reason": (None if independent_model_id else "same_model_as_author")}
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.author_request:
        if not args.brief or args.materials or args.actor_ref or args.archive_id or args.as_of or args.ingest_narrative or args.ingest_request:
            parser.error("--author-request requires --brief and cannot be combined with export inputs")
        brief = PrehistoryAuthoringBrief.model_validate_json(Path(args.brief).read_text(encoding="utf-8"))
        packet = author_request(brief, narrative_text=Path(args.narrative).read_text(encoding="utf-8") if args.narrative else None)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"written": str(output.resolve()), "command": "author-request",
                          "brief_hash": packet["brief_hash"],
                          "narrative_sha256": packet.get("narrative", {}).get("sha256")}, ensure_ascii=False))
        return
    if not args.materials or not args.actor_ref or not args.archive_id or args.narrative or args.ingest_narrative or args.ingest_request:
        parser.error("export mode requires --materials, --actor-ref and --archive-id; --narrative is only for --author-request")
    materials = LifeMaterialsDraft.model_validate_json(Path(args.materials).read_text(encoding="utf-8"))
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    document = export_actor_archive(materials, args.actor_ref, args.archive_id, as_of=as_of)
    if args.brief:
        brief = PrehistoryAuthoringBrief.model_validate_json(Path(args.brief).read_text(encoding="utf-8"))
        if materials.world_started_at != brief.world_started_at:
            parser.error("materials differ from the bound original World start")
        validate_draft(brief, document)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(document.model_dump_json(indent=2) + "\n")
    print(json.dumps({"written": str(output.resolve()), "archive_id": document.archive_id,
                      "actor_ref": document.actor_ref, "records": len(document.records),
                      "archive_sha256": digest(document), "materials_sha256": digest(materials)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
